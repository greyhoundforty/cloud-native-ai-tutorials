# Demo

promptfoo eval suite with CI integration.

## Prerequisites
- Node.js >= 18, Python >= 3.10, Anthropic API key

## Quick Start
```bash
npm install -g promptfoo
export ANTHROPIC_API_KEY=<your-key>
promptfoo eval --config promptfooconfig.yaml
promptfoo view
```

See `.github/workflows/` for the CI workflow.
