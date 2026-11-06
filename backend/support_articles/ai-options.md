AI Options controls account-level AI and voice settings available to your Talk-Lee agents.

![The pipeline mode selector, offering a standard pipeline or realtime speech-to-speech](/docs/ai-options-01-pipeline-mode.webp)

Depending on your plan and permissions, settings can include:

- LLM/model selection
- Speech-to-text provider selection
- Voice provider and voice selection
- Voice preview
- Model testing
- Voice tuning
- Turn-taking/end-of-turn behaviour
- Latency benchmarking

## Pipeline Mode

**Pipeline Mode** — "Choose how this tenant's calls are processed" — offers two options: **Standard Pipeline** and **Realtime (speech-to-speech)**.

## Change AI settings

1. Open **AI Options**.
2. Review the currently selected providers/models.
3. Make the required change.
4. Preview voices before changing production behaviour.
5. Use **Test LLM** where available.
6. Select **Save Configuration**. Settings apply to the next call.
7. Run a test call after any major voice or turn-taking change.

![The language model settings beside the speech-to-text engine](/docs/ai-options-02-llm-stt.webp)

### LLM Model

The **LLM Model** panel shows the provider family, a model dropdown, and a description of the chosen model including its measured speed. Two dials adjust **creativity** (temperature) and **max length** (tokens). Check a selected model with the campaign before using it for live calls.

### Speech-to-Text

**Speech-to-Text** is the engine that hears the caller. Choose an engine from the dropdown; the panel describes its turn-detection behaviour.

### TTS Voice

**TTS Voice** has provider tabs showing how many voices each offers, a **TTS Model** dropdown, and the voice grid.

### Test LLM

The **Test LLM** panel sends a message to the selected model so you can check its reply before saving.

## Preview a voice

Each voice card carries a play control, and the currently selected voice carries a **Preview** control. Select it to hear a sample. The control changes appearance while the sample is playing.

![The voice library, with provider tabs and a grid of available voices](/docs/ai-options-03-voices.webp)

![The selected voice, with a preview control](/docs/ai-options-04-selected-voice.webp)

## Voice tuning

Voice tuning adjusts conversational rhythm for your account. Each field is optional and falls back to a default when unset.

![Voice tuning, with sliders for turn-taking confidence and timing](/docs/ai-options-05-voice-tuning.webp)

Typical controls include:

- End-of-turn confidence
- End-of-turn silence timeout
- Eager-mode threshold and whether eager mode is disabled
- Minimum confidence and minimum character count for the first turn

Changes apply to the next call after saving. Use **Reset to default** to clear an override.

Change one setting at a time and test, so you can tell which change caused which effect.

## Latency metrics and benchmarking

The Latency Metrics panel reports real-time pipeline performance, such as time to first token from the language model, total model time, time to first audio, and total speech time.

![Latency metrics, with a benchmark control and timing cards](/docs/ai-options-06-latency.webp)

Select **Run Benchmark** to measure the current configuration. Run a benchmark after changing providers or models so you can compare like for like.

## Important

Campaign-specific voice settings can override account defaults where the campaign supports per-campaign voice selection.
