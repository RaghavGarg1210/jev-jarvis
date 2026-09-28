# Providers and local setup

Jev-Jarvis separates **planning** from **decision-making**. A planner proposes supported actions and arguments. A decider classifies the request. Neither provider receives authority to run arbitrary code: the application's validated adapters execute actions.

The default rules mode needs no third-party runtime dependencies or model download. Install the project in a virtual environment, then set optional provider variables in the same terminal before starting Jarvis using the [README](../README.md).

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Configuration

| Variable | Default | Supported values or purpose |
| --- | --- | --- |
| `JARVIS_DECIDER` | `rules` | `rules`, `laya`, `jev` |
| `JARVIS_PLANNER` | `rules` | `rules`, `ollama` |
| `JARVIS_JEV_MODEL` | `jev-latest` | TypeSafe model ID or alias |
| `TYPESAFE_API_KEY` | Unset | Required for hosted Jev |
| `JARVIS_OLLAMA_MODEL` | `qwen3:4b` | Installed Ollama model used for planning |
| `JARVIS_VOICE` | Disabled | Set to `1` to enable optional transcription |
| `JARVIS_WHISPER_MODEL` | `base.en` | Faster Whisper model name or local model path |

Provider selection is explicit. An unavailable local provider must not silently upload a request to a hosted alternative. Rules mode is deterministic command handling, not a local language model.

## Local Laya decisions

```sh
python -m pip install -e '.[laya]'
export JARVIS_DECIDER=laya
```

The optional dependency is pinned to **Laya 0.3.21**, whose release documentation was checked on PyPI. The adapter uses a lazily initialized `Router`; the first inference can download model files and take substantially longer than later requests. PyTorch and model weights make this a larger installation than the base project. [Laya package](https://pypi.org/project/laya/).

The native interface is:

```python
from laya import Router

router = Router()
result = router.predict(
    {"request": "Open Safari"},
    {
        "intent": {
            "type": "choice",
            "instructions": "Which supported action is requested?",
            "criteria": {
                "open_app": "Open a named application",
                "unknown": "Unsupported or unclear request",
            },
        }
    },
)
answer = result["answers"]["intent"]
```

The response is a dictionary. Choice answers contain `choice`, `probabilities`, `confidence`, and `answer_confidence`. Laya's `answer_confidence` is the reported answer's probability; its `confidence` describes distribution concentration. Jarvis reads `probabilities[selected_intent]` directly for both providers and labels it `selected_label_probability`. The native `action.act_probability` field is model output, not permission to execute. [Agent source](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py).

Checkpoints are not interchangeable: the English checkpoint has a 512-token default context, while multilingual and typed-decisions checkpoints default to 1,024. Instructions and options share the budget with state. Laya's project warns that long option lists can collapse distinguishable labels and shipped confidence may be overconfident. Its multilingual checkpoint can use a larger configured context, but this application keeps requests compact. [Official Laya repository](https://github.com/NandhaKishorM/laya).

Laya is Apache-2.0 software by Convai Innovations, independent of TypeSafe. After required weights are cached, its inference can run locally. A Python package version pin does not also pin every downloaded model revision.

## Hosted Jev decisions

```sh
export JARVIS_DECIDER=jev
export JARVIS_JEV_MODEL=jev-latest
export TYPESAFE_API_KEY='your-key'
```

Keep keys in the shell environment; do not paste real keys into committed examples or browser code. This provider sends the request state and decision questions to **TypeSafe**. It is not an offline mode.

The adapter calls the fixed endpoint `https://api.typesafe.ai/v1/systemone` using bearer authentication. Its request shape is:

```json
{
  "model": "jev-latest",
  "state": {"request": "Open Safari"},
  "questions": {
    "intent": {
      "type": "choice",
      "instructions": "Which supported action is requested?",
      "criteria": {
        "open_app": "Open a named application",
        "unknown": "Unsupported or unclear request"
      }
    }
  }
}
```

Responses have `model`, `answers`, and `usage`. A Choice answer supplies `type`, `choice`, `probabilities`, and `confidence`; it does not return generated prose. `questions` and `answers` are maps keyed by the same question IDs. [TypeSafe HTTP reference](https://docs.typesafe.ai/api).

`jev-latest` is a moving alias. On the research date it pointed to `jev-1.13.0`; a fixed version is preferable when comparing model behavior over time. Models accept textual state, not raw microphone recordings. [TypeSafe model reference](https://docs.typesafe.ai/models).

Confidence thresholds are application policy, not a reliability guarantee. Jarvis uses the probability assigned to the selected label, with an application floor of `0.65`; it does not compare the providers' different native `confidence` metrics. The same numerical floor does not establish equal calibration or accuracy across models. [TypeSafe confidence](https://docs.typesafe.ai/confidence).

## Local Ollama planning

Install and start Ollama separately, then download the desired model:

```sh
ollama pull qwen3:4b
export JARVIS_PLANNER=ollama
export JARVIS_OLLAMA_MODEL=qwen3:4b
```

Jarvis contacts only `http://127.0.0.1:11434` for this integration. The configured model must already be available in that Ollama installation. Startup of Jarvis does not install Ollama or pull its weights.

Ollama's `/api/chat` accepts a JSON schema through `format`. The application validates the returned action objects before execution. A schema restricts structure; it does not ensure that the interpretation is correct. [Ollama structured-output API](https://docs.ollama.com/capabilities/structured-outputs).

This is the optional generative component. Laya and Jev remain decision models; they do not compose messages or serve as conversational LLM replacements.

## Optional local transcription

```sh
python -m pip install -e '.[voice]'
export JARVIS_VOICE=1
export JARVIS_WHISPER_MODEL=base.en
```

Faster Whisper runs speech recognition locally and downloads model files on first use when given a model name. The default `base.en` is English-only. A local model directory can avoid a runtime model download. [Faster Whisper usage](https://github.com/SYSTRAN/faster-whisper#usage).

Allow microphone access in the browser/macOS prompt when using voice. Transcribed text follows the selected planning and decision providers: local transcription combined with hosted Jev still sends that text to TypeSafe. Voice input does not bypass previews or confirmations.

## What verification means

Provider-contract tests use controlled responses to check parsing, validation, and failure behavior without accounts, large downloads, or live actions. Passing those tests is not evidence that a particular model understands every command. Verify live inference separately after configuring a provider, begin with a harmless preview, and inspect the selected intent and arguments before using sensitive actions.

No hardware latency or command-accuracy benchmark is claimed by this repository. For design reasoning and comparison with existing assistants, see [research notes](RESEARCH.md).
