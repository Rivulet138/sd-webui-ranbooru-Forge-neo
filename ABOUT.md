# About Ranbooru Forge Neo

Ranbooru is a Forge Neo workbench for retrieving Booru tags, keeping a local SQLite prompt cache, and writing a selected prompt into txt2img or img2img. It is designed for repeated prompt work, so the current source, cache position, write mode, and last operation remain visible.

The extension keeps the original tag prompt intact and focuses on Booru search, tag cleanup, caching, and prompt handoff. Ranbooru sends selected cached Tag prompts to LLM Prompt Studio through the shared `prompt_batch.v1` format; model conversion, expansion, and polishing are handled there.

The txt2img panel keeps the historical IDs `ranbooru_tags`, `ranbooru_tag_prompt`, and `ranbooru_generate_prompt` for integrations. The img2img panel uses suffixed IDs such as `ranbooru_tag_prompt_img2img`, preventing duplicate DOM IDs while preserving existing txt2img automation.

This project is community maintained. Forge owns the host UI, generation settings, and third-party provider credentials. Ranbooru only installs its declared HTTP cache dependency and stores its local cache under the Forge user directory.

