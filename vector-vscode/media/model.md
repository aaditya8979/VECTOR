# 🤖 Get a Code Model

VECTOR works with any local code model. Here's what we recommend:

### Recommended: Qwen 2.5 Coder 7B
```bash
ollama pull qwen2.5-coder:7b
```
- 4.5 GB download, runs on 8GB RAM
- Highest accuracy with VECTOR's verification pipeline

### Alternatives
| Model | Command | Size |
|:---|:---|:---:|
| DeepSeek Coder V2 | `ollama pull deepseek-coder-v2` | 8.9 GB |
| CodeLlama 7B | `ollama pull codellama:7b` | 3.8 GB |
| StarCoder2 3B | `ollama pull starcoder2:3b` | 1.7 GB |

### Apple Silicon Users (fastest option)
```bash
pip install mlx-lm
```
Then download a Qwen 2.5 Coder model in MLX format from HuggingFace.
