# Fine-tuning Llama 3.2 3B with LoRA on a Home Lab GPU Using Unsloth

> **Goal:** Fine-tune a Llama 3.2 3B model on a custom Q&A dataset using LoRA and 4-bit quantization, then export to GGUF and serve with Ollama — all on a single consumer GPU.

---

## Why LoRA + Unsloth?

Full fine-tuning a 7B+ model requires 80 GB+ of VRAM and days of compute. **LoRA** (Low-Rank Adaptation) sidesteps this by training only small adapter matrices injected into the attention layers — typically less than 1% of total parameters. **Unsloth** makes LoRA practical on home hardware by rewriting the training kernels in Triton, yielding 2×–5× faster training and 70% less VRAM use compared to standard HuggingFace + PEFT.

**What you'll build:** A domain-specialized Q&A assistant fine-tuned on a small custom dataset, exported to GGUF, and served locally through Ollama.

---

## 1. Hardware Requirements

| GPU | VRAM | Max model size (4-bit LoRA) | Notes |
|-----|------|-----------------------------|-------|
| RTX 3060 | 12 GB | 7B | Tight; reduce batch size |
| RTX 3090 / 4090 | 24 GB | 13B comfortably | Sweet spot for home lab |
| A100 40G (cloud) | 40 GB | 30B | Rental ~$1–2/hr on Lambda/RunPod |
| A100 80G (cloud) | 80 GB | 70B | ~$2–3/hr |

**Minimum for this tutorial:** RTX 3090 or RTX 4090 (24 GB). If you only have a 16 GB card, reduce `max_seq_length` to 1024 and `per_device_train_batch_size` to 1.

**CPU RAM:** 32 GB recommended (model weights stage through RAM during loading).

**Storage:** 20 GB free for model weights and checkpoints.

---

## 2. Environment Setup

### Install Unsloth

Unsloth requires CUDA 11.8+ and Python 3.10+. Install into a fresh virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate

# Install PyTorch with CUDA 12.1 (adjust for your CUDA version)
pip install torch --index-url https://download.pytorch.org/whl/cu121

# Install Unsloth — pinned to a recent stable release
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
pip install --no-deps trl peft accelerate bitsandbytes
pip install datasets transformers sentencepiece
```

Verify your GPU is visible:

```python
import torch
print(torch.cuda.get_device_name(0))  # e.g. NVIDIA GeForce RTX 4090
print(torch.cuda.get_device_properties(0).total_memory // 1024**3, "GB")
```

---

## 3. Dataset Preparation

### Format: Alpaca-style instruction pairs

Unsloth's `FastLanguageModel` works best with the Alpaca prompt template. Each training example is a dict with three keys:

```python
{
    "instruction": "What is the boiling point of water at sea level?",
    "input": "",           # optional context; leave empty if not needed
    "output": "100 degrees C (212 degrees F) at standard atmospheric pressure (1 atm)."
}
```

### Build your JSONL dataset

```python
# prepare_dataset.py
import json, pathlib, random

# Your raw Q&A pairs — replace with your domain data
raw_pairs = [
    ("What does kubectl get pods do?", "Lists all pods in the current namespace along with their status, restarts, and age."),
    ("How do I scale a Deployment?", "Run: kubectl scale deployment <name> --replicas=<count>"),
    # ... add hundreds more
]

def make_example(q, a):
    return {"instruction": q, "input": "", "output": a}

examples = [make_example(q, a) for q, a in raw_pairs]
random.shuffle(examples)

# 90/10 train/eval split
split = int(len(examples) * 0.9)
train_data = examples[:split]
eval_data  = examples[split:]

pathlib.Path("data").mkdir(exist_ok=True)
with open("data/train.jsonl", "w") as f:
    for ex in train_data:
        f.write(json.dumps(ex) + "\n")

with open("data/eval.jsonl", "w") as f:
    for ex in eval_data:
        f.write(json.dumps(ex) + "\n")

print(f"Train: {len(train_data)}, Eval: {len(eval_data)}")
```

**Dataset size guidance:** 200–500 high-quality pairs is enough for domain adaptation with LoRA. More is better, but quality beats quantity — noisy labels degrade the model faster than small dataset size hurts it.

### Load with HuggingFace Datasets

```python
from datasets import load_dataset

dataset = load_dataset("json", data_files={
    "train": "data/train.jsonl",
    "validation": "data/eval.jsonl"
})
```

---

## 4. Load Model with 4-bit Quantization

```python
# train.py
from unsloth import FastLanguageModel
import torch

MAX_SEQ_LENGTH = 2048   # Reduce to 1024 if you're on <24 GB VRAM
DTYPE = None            # None = auto-detect; or torch.float16 / torch.bfloat16
LOAD_IN_4BIT = True     # 4-bit quantization via bitsandbytes

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Llama-3.2-3B-Instruct",  # ~6 GB download
    max_seq_length = MAX_SEQ_LENGTH,
    dtype = DTYPE,
    load_in_4bit = LOAD_IN_4BIT,
)
```

Unsloth automatically downloads from HuggingFace Hub and patches the model kernels. First run takes a few minutes; subsequent runs load from cache.

---

## 5. Attach LoRA Adapters

```python
model = FastLanguageModel.get_peft_model(
    model,
    r = 16,                     # LoRA rank — higher = more capacity, more VRAM
    target_modules = [          # Which attention projections to adapt
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    lora_alpha = 16,            # Scaling factor — typically set equal to r
    lora_dropout = 0,           # 0 works well with Unsloth's optimizations
    bias = "none",
    use_gradient_checkpointing = "unsloth",  # Unsloth's optimized checkpointing
    random_state = 42,
    use_rslora = False,
    loftq_config = None,
)
```

### Key LoRA hyperparameters explained

| Param | What it controls | Typical range |
|-------|-----------------|---------------|
| `r` (rank) | Number of trainable dimensions per layer. Higher = more expressive but more VRAM and risk of overfitting | 8–64 |
| `lora_alpha` | Scales the LoRA output: `alpha/r * BA*x`. Keep equal to `r` for a neutral scale | 8–64 |
| `lora_dropout` | Regularization. Unsloth sets to 0 by default because the kernel handles it | 0–0.1 |
| `target_modules` | Which layers get adapters. Include MLP projections for stronger domain shift | varies |

**Rule of thumb for home lab:** Start with `r=16, lora_alpha=16`. If you see underfitting (loss plateaus high), try `r=32`. If you see overfitting (eval loss rises while train loss falls), reduce to `r=8` or add dropout.

---

## 6. Format Prompts and Train

### Define the Alpaca prompt template

```python
alpaca_prompt = """Below is an instruction that describes a task. Write a response that appropriately completes the request.

### Instruction:
{}

### Input:
{}

### Response:
{}"""

EOS_TOKEN = tokenizer.eos_token

def format_prompts(examples):
    instructions = examples["instruction"]
    inputs       = examples["input"]
    outputs      = examples["output"]
    texts = []
    for instruction, inp, output in zip(instructions, inputs, outputs):
        text = alpaca_prompt.format(instruction, inp, output) + EOS_TOKEN
        texts.append(text)
    return {"text": texts}

dataset = dataset.map(format_prompts, batched=True)
```

### Training with TRL's SFTTrainer

```python
from trl import SFTTrainer
from transformers import TrainingArguments
from unsloth import is_bfloat16_supported

trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset["train"],
    eval_dataset  = dataset["validation"],
    dataset_text_field = "text",
    max_seq_length = MAX_SEQ_LENGTH,
    dataset_num_proc = 2,
    packing = False,   # Set True for short sequences to improve throughput
    args = TrainingArguments(
        per_device_train_batch_size  = 2,
        gradient_accumulation_steps  = 4,   # Effective batch = 2*4 = 8
        warmup_steps                 = 5,
        num_train_epochs             = 3,   # 1–3 for small datasets
        learning_rate                = 2e-4,
        fp16                         = not is_bfloat16_supported(),
        bf16                         = is_bfloat16_supported(),
        logging_steps                = 1,
        optim                        = "adamw_8bit",
        weight_decay                 = 0.01,
        lr_scheduler_type            = "linear",
        seed                         = 42,
        output_dir                   = "outputs",
        eval_strategy                = "epoch",
        save_strategy                = "epoch",
        load_best_model_at_end       = True,
        metric_for_best_model        = "eval_loss",
        report_to                    = "none",   # Set to "wandb" to track runs
    ),
)

# Show trainable parameter count
trainer_stats = trainer.train()
print(f"Training time: {trainer_stats.metrics['train_runtime']:.1f}s")
print(f"Samples/sec:   {trainer_stats.metrics['train_samples_per_second']:.2f}")
```

### Expected training output

```
{'loss': 2.134, 'learning_rate': 0.0002, 'epoch': 0.1}
{'loss': 1.876, 'learning_rate': 0.00019, 'epoch': 0.2}
...
{'eval_loss': 0.92, 'epoch': 1.0}
...
{'eval_loss': 0.78, 'epoch': 3.0}
```

On an RTX 4090 with 300 training examples, 3 epochs takes roughly **3–5 minutes** with Unsloth. The same run with vanilla HuggingFace + PEFT takes 15–20 minutes.

---

## 7. Evaluation: Base vs. Fine-tuned

Before exporting, qualitatively compare the base model and your fine-tuned model on held-out prompts:

```python
# Switch model to inference mode
FastLanguageModel.for_inference(model)

def ask(instruction, inp=""):
    prompt = alpaca_prompt.format(instruction, inp, "")
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    outputs = model.generate(
        **inputs,
        max_new_tokens = 256,
        temperature = 0.3,
        do_sample = True,
        pad_token_id = tokenizer.eos_token_id,
    )
    return tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

# Test with a domain-specific question
q = "How do I roll back a failed Helm release?"
print("Fine-tuned:", ask(q))
```

To compare against the base model, reload without the LoRA adapters:

```python
base_model, _ = FastLanguageModel.from_pretrained(
    "unsloth/Llama-3.2-3B-Instruct",
    max_seq_length=MAX_SEQ_LENGTH,
    load_in_4bit=True
)
FastLanguageModel.for_inference(base_model)
# run the same ask() function against base_model
```

**What to look for:** The fine-tuned model should give more specific, domain-aware answers. If you see catastrophic forgetting (garbled outputs on general questions), reduce `num_train_epochs` or lower the learning rate.

---

## 8. Export to GGUF and Serve with Ollama

### Save as GGUF (Q4_K_M quantization)

```python
# Export to GGUF with 4-bit K-quantization — best quality/size tradeoff
model.save_pretrained_gguf(
    "llama32-3b-lora-finetuned",
    tokenizer,
    quantization_method = "q4_k_m"
)
# Output: llama32-3b-lora-finetuned/unsloth.Q4_K_M.gguf (~2 GB)
```

Unsloth handles the GGUF conversion internally via `llama.cpp`. No separate conversion step needed.

### Create an Ollama Modelfile

```
FROM ./llama32-3b-lora-finetuned/unsloth.Q4_K_M.gguf

TEMPLATE """Below is an instruction that describes a task. Write a response that appropriately completes the request.

### Instruction:
{{ .Prompt }}

### Input:


### Response:
"""

PARAMETER stop "### Instruction:"
PARAMETER stop "### Input:"
PARAMETER temperature 0.3
PARAMETER top_p 0.9
```

Save that as `Modelfile`, then:

```bash
# Register with Ollama
ollama create llama32-qa-tuned -f Modelfile

# Test it
ollama run llama32-qa-tuned "How do I roll back a failed Helm release?"
```

Expected output (fine-tuned behavior):

```
Run `helm rollback <release-name> <revision>` to roll back to a previous revision.
Use `helm history <release-name>` to list available revisions first.
```

---

## 9. Complete Training Script

`train.py` — all steps in one file:

```python
#!/usr/bin/env python3
"""
LoRA fine-tuning with Unsloth on Llama 3.2 3B.
Requirements: unsloth, trl, datasets, transformers
"""
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import SFTTrainer
from transformers import TrainingArguments
from datasets import load_dataset

MAX_SEQ_LENGTH = 2048
MODEL_NAME     = "unsloth/Llama-3.2-3B-Instruct"
OUTPUT_DIR     = "llama32-qa-lora"

# Load model
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_NAME, max_seq_length=MAX_SEQ_LENGTH,
    dtype=None, load_in_4bit=True,
)

# Attach LoRA adapters
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth", random_state=42,
)

# Dataset
alpaca_prompt = (
    "Below is an instruction that describes a task. "
    "Write a response that appropriately completes the request.\n\n"
    "### Instruction:\n{}\n\n### Input:\n{}\n\n### Response:\n{}"
)

def format_prompts(examples):
    return {"text": [
        alpaca_prompt.format(i, inp, o) + tokenizer.eos_token
        for i, inp, o in zip(examples["instruction"], examples["input"], examples["output"])
    ]}

dataset = load_dataset("json", data_files={"train":"data/train.jsonl","validation":"data/eval.jsonl"})
dataset = dataset.map(format_prompts, batched=True)

# Train
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer,
    train_dataset=dataset["train"], eval_dataset=dataset["validation"],
    dataset_text_field="text", max_seq_length=MAX_SEQ_LENGTH,
    args=TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=2, gradient_accumulation_steps=4,
        num_train_epochs=3, learning_rate=2e-4, weight_decay=0.01,
        warmup_steps=5, lr_scheduler_type="linear",
        fp16=not is_bfloat16_supported(), bf16=is_bfloat16_supported(),
        optim="adamw_8bit", logging_steps=1,
        eval_strategy="epoch", save_strategy="epoch",
        load_best_model_at_end=True, metric_for_best_model="eval_loss",
        report_to="none", seed=42,
    ),
)
trainer.train()

# Export to GGUF
model.save_pretrained_gguf(OUTPUT_DIR, tokenizer, quantization_method="q4_k_m")
print(f"Saved to {OUTPUT_DIR}/unsloth.Q4_K_M.gguf")
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `CUDA out of memory` | Batch too large | Reduce `per_device_train_batch_size` to 1, increase `gradient_accumulation_steps` |
| Loss not decreasing | LR too low or dataset too small | Try `lr=5e-4` or add more data |
| `eval_loss` rises after epoch 1 | Overfitting | Reduce `num_train_epochs` to 1–2 or lower `r` |
| Garbled output | EOS token missing from training data | Ensure `+ tokenizer.eos_token` appended in `format_prompts` |
| Ollama returns generic answers | Modelfile template mismatch | Verify `TEMPLATE` matches the Alpaca format used during training |
| Unsloth install fails | CUDA version mismatch | Check `nvcc --version` matches your PyTorch CUDA build |

---

## Summary

You've fine-tuned Llama 3.2 3B with LoRA using Unsloth, getting 2–5x faster training and 70% less VRAM vs. vanilla PEFT. The workflow:

1. **Prepare** instruction-following pairs as JSONL
2. **Load** the model in 4-bit with `FastLanguageModel.from_pretrained`
3. **Attach** LoRA adapters with `get_peft_model` (`r=16` is a solid default)
4. **Train** with `SFTTrainer` — 3 epochs, lr=2e-4, effective batch size 8
5. **Evaluate** by comparing base vs. fine-tuned on held-out examples
6. **Export** to GGUF with `save_pretrained_gguf` and serve via Ollama

From dataset prep to a running Ollama endpoint: under 30 minutes on an RTX 4090.
