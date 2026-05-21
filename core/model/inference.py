"""
Inference engine — wraps mlx-lm for native Metal GPU inference on Apple Silicon,
and Ollama for cross-platform GPU/CPU inference.
Uses Qwen 2.5 Coder with ChatML prompt formatting for AST function generation.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from config import TEMPERATURE, MAX_TOKENS_OUT
from core.model.grammar import SYSTEM_PROMPT


# Default model path — override via TSDC_MLX_MODEL_PATH env var
_DEFAULT_MODEL_PATH = os.path.expanduser("~/models/qwen25-coder-mlx/")


class OllamaBackend:
    """
    Ollama backend — works on Mac (Intel/ARM), Windows, Linux.
    Requires: ollama serve + ollama pull qwen2.5-coder:7b
    API: http://localhost:11434/v1 (OpenAI-compatible)
    Speed: 20-60 TPS depending on GPU (CUDA/Metal/CPU)
    """
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5-coder:7b"):
        self.base_url = base_url
        self.model = model

    def is_available(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def generate_function(
        self,
        tsdc_document: str,
        grammar_mode: str = "relaxed",
        max_tokens: int = MAX_TOKENS_OUT,
        temperature: float = TEMPERATURE,
    ) -> tuple[str, dict]:
        import requests
        import time
        
        t0 = time.time()
        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": tsdc_document},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
            timeout=180,
        )
        r.raise_for_status()
        
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
        elapsed = time.time() - t0
        usage = data.get("usage", {})
        out_toks = usage.get("completion_tokens", 0)
        
        return InferenceEngine._post_process_static(content), {
            "backend": "ollama",
            "model": self.model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": out_toks,
            "elapsed_sec": round(elapsed, 2),
            "tokens_per_sec": round(out_toks / max(elapsed, 0.001), 1),
            "grammar_mode": grammar_mode,
        }

    def generate_raw(self, prompt: str, max_tokens: int = 512) -> str:
        import requests
        r = requests.post(
            f"{self.base_url}/v1/completions",
            json={
                "model": self.model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "stream": False,
            },
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["response"].strip()


class InferenceEngine:
    _instance: Optional["InferenceEngine"] = None

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or os.environ.get(
            "TSDC_MLX_MODEL_PATH", _DEFAULT_MODEL_PATH
        )
        self._backend_type = None
        self._backend_impl = None
        
        # Used by MLX
        self._model = None
        self._tok = None
        self._generate_fn = None
        self._make_sampler = None
        
        self._load()

    def _load(self):
        flag = os.environ.get("TSDC_BACKEND", "auto").lower()

        # 1. Try MLX
        if flag in ("auto", "mlx") and self._try_load_mlx():
            self._backend_type = "mlx"
            return

        # 2. Try Ollama
        if flag not in ("mlx",):
            ollama_url = os.environ.get("TSDC_OLLAMA_URL", "http://localhost:11434")
            ollama_model = os.environ.get("TSDC_OLLAMA_MODEL", "qwen2.5-coder:7b")
            candidate = OllamaBackend(ollama_url, ollama_model)
            if candidate.is_available():
                self._backend_type = "ollama"
                self._backend_impl = candidate
                print(f"[inference] Ollama detected at {ollama_url} · model: {ollama_model}")
                return

        raise RuntimeError("No available inference backend found (tried MLX and Ollama).")

    def _try_load_mlx(self) -> bool:
        try:
            from mlx_lm import load, generate
            from mlx_lm.sample_utils import make_sampler
            self._generate_fn = generate
            self._make_sampler = make_sampler
        except ImportError:
            return False

        if not os.path.exists(self.model_path):
            return False

        print(f"[inference] Loading MLX model from {self.model_path}")
        t0 = time.time()
        self._model, self._tok = load(self.model_path)
        print(f"[inference] MLX model loaded in {time.time() - t0:.1f}s")
        return True

    @classmethod
    def get(cls) -> "InferenceEngine":
        """Singleton — load model once, reuse across all tasks."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _format_chatml(self, system: str, user: str) -> str:
        """Build a ChatML prompt string for Qwen 2.5 Coder."""
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def generate_function(
        self,
        tsdc_document: str,
        grammar_mode: str = "relaxed",
        max_tokens: int = MAX_TOKENS_OUT,
        temperature: float = TEMPERATURE,
    ) -> tuple[str, dict]:
        """
        Generate a complete Python function from a TSDC document.
        Returns (function_text, stats_dict).
        
        temperature: 0.0 for attempt 1 (deterministic), 0.3 for retries
                     to break out of repetitive failure loops.
        """
        if self._backend_type == "ollama":
            return self._backend_impl.generate_function(
                tsdc_document, grammar_mode, max_tokens, temperature=temperature
            )
            
        # MLX path
        prompt = self._format_chatml(SYSTEM_PROMPT, tsdc_document)
        sampler = self._make_sampler(temp=temperature)

        # Tokenize input BEFORE starting the timer to accurately measure generation TPS
        if hasattr(self._tok, "apply_chat_template"):
            # If using proper apply_chat_template
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": tsdc_document},
            ]
            try:
                formatted = self._tok.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                if isinstance(formatted, str):
                    prompt = formatted
            except Exception:
                pass
            
        in_tokens = len(self._tok.encode(prompt))
        
        t0 = time.time()
        raw = self._generate_fn(
            self._model,
            self._tok,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=sampler,
            verbose=False,
        )
        elapsed = time.time() - t0

        out_tokens = len(self._tok.encode(raw)) if raw else 0

        content = self._post_process_static(raw.strip() if raw else "")

        stats = {
            "backend": "mlx",
            "prompt_tokens": in_tokens,
            "output_tokens": out_tokens,
            "elapsed_sec": round(elapsed, 2),
            "tokens_per_sec": round(out_tokens / max(elapsed, 0.001), 1),
            "grammar_mode": grammar_mode,
        }

        return content, stats

    def generate_raw(
        self,
        prompt: str,
        max_tokens: int = 512,
    ) -> str:
        """Unconstrained generation — used for Knowledge Item extraction."""
        if self._backend_type == "ollama":
            return self._backend_impl.generate_raw(prompt, max_tokens)

        sampler = self._make_sampler(temp=0.1)
        raw = self._generate_fn(
            self._model,
            self._tok,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=sampler,
            verbose=False,
        )
        return raw.strip() if raw else ""

    # All possible function-start keywords across supported languages
    _FUNC_PREFIXES = (
        "def ",           # Python
        "async def ",     # Python async
        "function ",      # JS/TS
        "async function ",# JS/TS async
        "export function ",# TS
        "export default function ",  # TS
        "export async function ",    # TS
        "func ",          # Go
        "func(",          # Go method (receiver)
        "fn ",            # Rust
        "pub fn ",        # Rust pub
        "pub async fn ",  # Rust
        "async fn ",      # Rust async
        "pub(crate) fn ", # Rust visibility
    )

    # C/C++ return type patterns — matched via regex
    _C_FUNC_RE = None

    @classmethod
    def _get_c_func_re(cls):
        if cls._C_FUNC_RE is None:
            import re
            cls._C_FUNC_RE = re.compile(
                r'^(?:static\s+|inline\s+|extern\s+|virtual\s+|constexpr\s+)*'
                r'(?:(?:void|int|float|double|char|bool|auto|size_t|string|'
                r'std::\w+|const\s+\w+|unsigned\s+\w+|\w+)\s+\*?\s*)'
                r'\w+\s*\('
            )
        return cls._C_FUNC_RE

    # Keywords that indicate code, not prose — for all languages
    _CODE_PREFIXES = (
        "def ", "class ", "@", "import ", "from ", "#",   # Python
        "function ", "export ", "const ", "let ", "var ",  # JS/TS
        "func ", "package ", "type ", "import (",          # Go
        "fn ", "pub ", "use ", "mod ", "struct ", "impl ", "trait ",  # Rust
        "void ", "int ", "float ", "double ", "char ", "bool ",       # C/C++
        "auto ", "static ", "inline ", "namespace ", "#include ",
        "//", "/*", "///",                                 # Comments
    )

    @classmethod
    def _post_process_static(cls, raw: str) -> str:
        """
        Extract the clean function body from raw model output.
        Strategy: XML <code> extraction first → markdown fences → regex prose stripping.
        Platform-agnostic: works identically on MLX (Mac) and Ollama (Windows).
        """
        import re

        # Strip ChatML tokens
        raw = raw.replace("<|im_end|>", "").replace("<|im_start|>", "")

        # ── Strategy 1: XML <code> tag extraction (preferred) ─────────────────
        # If the model obeyed our XML-structured prompt, extract from <code> tags.
        # ALWAYS take the LAST non-empty block — that's the model's final answer.
        code_blocks = re.findall(r'<code>\s*\n?(.*?)\n?\s*</code>', raw, re.DOTALL)
        if code_blocks:
            # Take the last non-empty block (model's final answer)
            for block in reversed(code_blocks):
                extracted = block.strip()
                if not extracted:
                    continue
                # Validate it actually contains a function definition
                if any(extracted.lstrip().startswith(p) for p in cls._FUNC_PREFIXES):
                    return extracted
                c_re = cls._get_c_func_re()
                if c_re.match(extracted.lstrip()):
                    return extracted
                # Even if it doesn't start with a func prefix, if it's the only
                # block and non-empty, return it (let downstream handle it)
                if len(code_blocks) == 1:
                    return extracted

        # ── Strategy 2: Markdown fence extraction ─────────────────────────────
        raw = re.sub(r"```(?:\w+)?\n?", "", raw)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

        # ── Strategy 3: Regex-based function boundary detection (fallback) ────
        lines = raw.splitlines(keepends=True)
        start = None
        c_re = cls._get_c_func_re()

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            # Check known keyword prefixes
            if any(stripped.startswith(p) for p in cls._FUNC_PREFIXES):
                start = i
                break
            # Check C/C++ return-type patterns
            if c_re.match(stripped):
                start = i
                break

        if start is None:
            return raw.strip()  # return as-is; let downstream pipeline handle it

        # Find where the function cleanly ends
        end = len(lines)
        base_indent = len(lines[start]) - len(lines[start].lstrip())

        for i in range(start + 1, len(lines)):
            stripped = lines[i].rstrip("\n")
            if not stripped:
                continue

            current_indent = len(lines[i]) - len(lines[i].lstrip())

            # Dedent below function base — definitely outside the function
            if current_indent < base_indent and not lines[i].lstrip().startswith(("#", "//", "/*")):
                end = i
                break

            # Stop at unindented lines that don't look like code
            # (catches short prose like "This is prose." or "Hope that helps.")
            if current_indent == 0:
                if not any(stripped.startswith(p) for p in cls._CODE_PREFIXES):
                    end = i
                    break

        return "".join(lines[start:end]).strip()
    
    def _post_process(self, raw: str) -> str:
        return self._post_process_static(raw)