# DISCLAIMER

> **⚠️ READ THIS BEFORE USING PROMPTCRAFT.**
> By downloading, installing, or running this software, you confirm that you have read, understood, and agreed to all terms in this disclaimer.

---

## 1. No Warranty

PromptCraft is provided **"AS IS"**, without warranty of any kind — express, implied, or statutory — including but not limited to the implied warranties of merchantability, fitness for a particular purpose, title, and non-infringement. The author(s) make no representation that the software will meet your requirements, operate without interruption, or be free of errors, bugs, or security vulnerabilities.

---

## 2. AI-Generated Content — Inherent Limitations

PromptCraft uses artificial intelligence (via locally-hosted large language models) to generate prompt suggestions, technique recommendations, and synthesized outputs. **AI models can and do produce incorrect, biased, misleading, or inappropriate content.**

You must be aware that:

- Generated prompts may be **ineffective, poorly structured, or unsuitable** for your intended use case, even when produced by a high-quality model.
- The AI has no knowledge of your downstream platform, API, content policies, or production environment. A prompt that works in one context may fail, produce unexpected results, or violate terms of service in another.
- Technique recommendations made by the LLM are probabilistic suggestions — not authoritative guidance. They may be suboptimal or incorrect for your specific task.
- The quality of all output is directly determined by the model you choose to run. Smaller or less capable models will produce less reliable results. **The author(s) have no control over any model's behavior.**

**You are solely responsible for evaluating, testing, and validating any prompt before deploying it in a real system.**

---

## 3. Plugin System — Arbitrary Code Execution Risk

PromptCraft supports a plugin system that can load and execute **arbitrary Python code** via `plugin.py` hook files placed in the `plugins/` directory.

You must be aware that:

- Any `plugin.py` file placed in the `plugins/` directory **will be imported and executed** by PromptCraft at startup without sandboxing or code review.
- Malicious or poorly written plugins can read, write, or delete files on your system; exfiltrate data; consume system resources; or cause other harmful effects.
- **Only install plugins from sources you fully trust.** Treat plugin files with the same caution you would apply to any third-party script you run on your machine.
- The author(s) of PromptCraft are not responsible for any damage caused by plugins created, distributed, or installed by third parties.

---

## 4. File System Access

PromptCraft writes files to your local file system, including:

- `promptcraft_config.json` — saved backend and model configuration
- `custom_domains.json` — user-created domain definitions
- Files in the `./prompts/` directory — exported prompt outputs

While path traversal protections are in place, the author(s) make no guarantee that these protections are complete or bypass-proof. Users should be aware that the tool has read/write access to the working directory and its subdirectories.

---

## 5. Local Model & Backend Dependency

PromptCraft relies on third-party local LLM backends (**Ollama** and/or **LM Studio**). The author(s) of PromptCraft:

- Have no control over the behavior, accuracy, safety, or content policies of any model hosted through these backends.
- Are not responsible for model outputs of any kind — including outputs that are harmful, offensive, incorrect, or legally problematic.
- Are not responsible for changes in Ollama, LM Studio, or any model that affect PromptCraft's functionality or output quality.

The `rich` and `pyperclip` dependencies are third-party packages maintained independently. The author(s) provide no warranty regarding their security, compatibility, or continued availability.

---

## 6. Limitation of Liability

To the maximum extent permitted by applicable law, in no event shall the author(s), contributors, or distributors of PromptCraft be liable for any:

- Direct, indirect, incidental, special, consequential, or exemplary damages
- Loss of data, revenue, profits, or business opportunities
- Damages arising from deploying AI-generated prompts in production systems
- Damages arising from third-party plugins executed by this software

arising out of or in connection with the use of, or inability to use, this software — regardless of whether such damages were foreseeable and regardless of the theory of liability (contract, tort, negligence, strict liability, or otherwise).

**This limitation applies even if the author(s) have been advised of the possibility of such damages.**

---

## 7. User Responsibility

By using PromptCraft, you agree that:

1. You will evaluate and test all AI-generated prompts before using them in any production, commercial, or critical system.
2. You will only install plugins from sources you trust and have reviewed.
3. You will not use PromptCraft to generate prompts intended to deceive, manipulate, harass, or harm others.
4. You will not use PromptCraft to circumvent content policies, access controls, or safety systems of any AI platform or API.
5. You are solely responsible for ensuring that prompts you generate and deploy comply with the terms of service of any platform or API you use them with.
6. You take full responsibility for any outcome resulting from prompts you choose to deploy.

---

## 8. No Guarantee of Prompt Effectiveness

PromptCraft is a tool to assist with prompt engineering. It does **not** guarantee that generated prompts will:

- Produce desired outputs from any AI model or system
- Be compatible with any specific API, platform, or model
- Remain effective as underlying models are updated or changed
- Meet quality, safety, or performance requirements for any particular use case

Prompt engineering is an iterative discipline. Generated outputs are starting points, not finished products.

---

## 9. No Affiliation

The project name "PromptCraft" and The author(s) are personal identifiers. This project is **not affiliated with, endorsed by, sponsored by, or connected to** NASA (National Aeronautics and Space Administration), any AI company (including but not limited to OpenAI, Anthropic, Google, or Meta), or any other organization. Any resemblance to official tools or products is coincidental.

---

## 10. Intended Use

PromptCraft is intended for **educational, personal, and development use**. Use for any unlawful purpose — including generating content that is illegal in your jurisdiction — is strictly prohibited.

Users are solely responsible for ensuring their use of this software, and any prompts generated by it, comply with all applicable local, national, and international laws and regulations.

---

## 11. No Support Obligation

The author(s) of PromptCraft have no obligation to provide support, maintenance, updates, bug fixes, or security patches. Issues may be reported via GitHub but responses are not guaranteed.

---

*Last updated: May 2026 — PromptCraft v0.2.0*
