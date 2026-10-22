AI Options controls account-level AI and voice settings available to your Talk-Lee agents.

Depending on your plan and permissions, settings can include:

- LLM/model selection
- Speech-to-text provider selection
- Voice provider and voice selection
- Voice preview
- Model testing
- Voice tuning
- Turn-taking/end-of-turn behaviour
- Latency benchmarking

## Change AI settings

1. Open **AI Options**.
2. Review the currently selected providers/models.
3. Make the required change.
4. Preview voices before changing production behaviour.
5. Use **Test LLM** where available.
6. Save the configuration.
7. Run a test call after any major voice or turn-taking change.

## Preview a voice

Each voice card carries a play control, and the currently selected voice carries a **Preview** control. Select it to hear a sample. The control changes appearance while the sample is playing.

## Voice tuning

Voice tuning adjusts conversational rhythm for your account. Each field is optional and falls back to a default when unset.

Typical controls include:

- End-of-turn confidence
- End-of-turn silence timeout
- Eager-mode threshold and whether eager mode is disabled
- Minimum confidence and minimum character count for the first turn

Changes apply to the next call after saving. Use **Reset to default** to clear an override.

Change one setting at a time and test, so you can tell which change caused which effect.

## Latency metrics and benchmarking

The Latency Metrics panel reports real-time pipeline performance, such as time to first token from the language model, total model time, time to first audio, and total speech time.

Select **Run Benchmark** to measure the current configuration. Run a benchmark after changing providers or models so you can compare like for like.

## Important

Campaign-specific voice settings can override account defaults where the campaign supports per-campaign voice selection.
