# Sensitive-content classifier — model selection decision

Resolves the open half of [issue #21](https://github.com/rohan21895/memory-engine/issues/21). The **mechanism** was settled by Codex and is shipped as `contracts/schemas/safety-clearance.schema.json`. This document settles **which model**, and it is the input to filling in `models/configs/nsfw-siglip-head.json`, which is currently a deliberately blocking placeholder.

**Status:** recommendation, not yet implemented. `blocks_commercial_release` stays `true` until the eval gate in §7 passes.

---

## 0. What was verified by execution, and what was not

This distinction matters more here than anywhere else in the stack, so it goes first.

**Verified by execution** — I fetched these over the network and read the returned bytes:

- The full text of three licence *files*: LAION's `license.md`, GantMan's `LICENSE.md`, Bumble's `LICENSE`, and Freepik's GitHub `LICENSE`.
- Hugging Face API metadata for eleven model repositories: declared licence, base model, declared datasets, complete file listing, and exact blob sizes in bytes.
- `config.json` and `labels.json` for Falconsai, Marqo, OwenElliott and Freepik — so the class labels and input resolutions in the table below are read from the model's own config, not from prose.
- The GitHub API licence field and archived/last-push state for six repositories.
- Apple's developer documentation JSON for `SensitiveContentAnalysis`, `SCSensitivityAnalyzer`, `SCSensitivityAnalysis`, `SCSensitivityAnalysisPolicy`, the entitlement page, and the "Detecting nudity in media" article.
- The ImageNet terms-of-access page.
- LAION's Re-LAION-5B announcement.
- crates.io and PyPI metadata confirming Rust and Python bindings for Apple's framework exist.

**Not verified — reasoned only.** No model was downloaded, loaded, converted or run. **Every accuracy number in this document is a vendor's own figure on the vendor's own test set, and none of them are comparable to each other.** No ONNX export was attempted. No image was classified. There is no measurement in this document, and the recommendation in §6 is therefore a design, not a result. §7 is the gate that would turn it into a result.

I am not a lawyer. The licence readings here are an engineering desk assessment, same standing as `docs/model-registry.md`.

---

## 1. What the decision has to satisfy

From the config, the schema, and the issue — all already decided, restated so the table can be scored against them:

1. **Fully on-device.** ONNX-exportable, CPU and CoreML viable. No API, no hosted endpoint, no phone-home.
2. **Three classes, not one flag** — `explicit` / `suggestive` / `medical_or_artistic`. The schema requires all three as independent `Unit` probabilities (`ClassScores`), and the config emits `[-1, 3]` logits through a sigmoid, i.e. three one-vs-rest heads, not a softmax over three.
3. **Permissive licence.** Non-commercial, AGPL, research-only and field-of-use-restricted licences are disqualifying. This project has already rejected CodeFormer, FLUX.1-dev, madmom and Essentia on these grounds.
4. **Training-data provenance is a first-class criterion**, weighted at least as heavily as the licence. A classifier trained on scraped adult material of unknown origin is a poor foundation for a safety claim in a product that handles families' private photographs and children's photographs.
5. **Runs on every file, not a sample.** Coverage is what a gate needs most, and `SafetyClearance.decision.cleared_for_publication` is false if even one item is `indeterminate` — so a classifier too expensive to run on everything doesn't just degrade the product, it blocks it.

Criterion 5 is stronger than it looks. A 4 B-parameter VLM that has to do a forward pass per photo cannot gate a 100 k-image library on a laptop, and a gate that times out produces `indeterminate`, which blocks the publication. **Cost is a correctness property here, not a performance one.**

---

## 2. Comparison table

Sizes are exact bytes from the Hugging Face API, converted to MB (10⁶). "Licence read?" is the distinction the whole audit turns on.

| # | Candidate | Exact licence + source URL | Licence read? | Training data & how documented | Classes emitted | ONNX viability | Size | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | **LAION CLIP-based-NSFW-Detector** (AutoKeras head over CLIP ViT-L/14 or B/32) | **MIT**, "Copyright 2022, Christoph Schuhmann" — [license.md](https://github.com/LAION-AI/CLIP-based-NSFW-Detector/blob/main/license.md). GitHub's licence detector reports `NOASSERTION` because the file is non-standard; the text itself is verbatim MIT. | **Yes — read in full.** | **Undocumented.** README publishes training *embeddings* via Google Drive and states they are "not fully manually annotated." No image source, no count, no annotator, no terms. See §4.1 for why this one is worse than merely undocumented. | **1** — a single 0–1 score. No classes. | AutoKeras/Keras SavedModel → `tf2onnx`. Plausible; **not attempted**. | Head only (a few MB); requires a CLIP tower we do not otherwise run. | **Reject.** No classes; undocumented provenance; wrong embedding space (CLIP ViT-L/14 768-d, not SigLIP 2 1152-d); repo last pushed 2023-05-30. |
| 2 | **Falconsai/nsfw_image_detection** | `license: apache-2.0` in HF card frontmatter — [model card](https://huggingface.co/Falconsai/nsfw_image_detection). | **No.** No `LICENSE` file exists in the repo; the entire licence is a metadata tag. | "a proprietary dataset comprising approximately 80,000 images" (verbatim). No source, no rights statement, no labelling method. Base is `google/vit-base-patch16-224-in21k` → ImageNet-21k (see §4.5). | **2** — `{normal, nsfw}` (read from `labels.json`). | ViT → ONNX is routine. | 343.2 MB fp32. | **Reject.** Binary; undocumented data; ImageNet base; and see §4.6 — the repo also ships a **YOLOv9-derived** `.pt` under an Apache-2.0 tag, which is a licence contradiction nobody has explained. |
| 3 | **Marqo/nsfw-image-detection-384** | `license: apache-2.0` in HF card frontmatter — [model card](https://huggingface.co/Marqo/nsfw-image-detection-384). | **No.** No `LICENSE` file in the repo. | "a proprietary dataset of 220,000 images" containing "real photos, drawings, Rule 34 material, memes, and AI-generated images" (verbatim). Labelling methodology explicitly not stated. Base `vit_tiny_patch16_384.augreg_in21k_ft_in1k` → ImageNet. | **2** — `["NSFW","SFW"]` (read from `config.json`). | timm ViT → ONNX is routine. | **22.4 MB** — the smallest credible off-the-shelf option. | **Reject as primary.** Binary, and "Rule 34 material" is a named category of scraped adult content with no rights position. Genuinely well-engineered; the objection is the data, not the model. |
| 4 | **OwenElliott/image-safety-classifier-s / -xs** | `license: mit` in HF card frontmatter — [model card](https://huggingface.co/OwenElliott/image-safety-classifier-s). | **No.** No `LICENSE` file in either repo. | "~320,000 images from web sources" incl. "real photos, drawings, Rule 34, screenshots, AI generated images, memes." Labels **validated using Marqo's own detector** with manual review of uncertain cases — so it inherits Marqo's provenance *and* its decision boundary. Base SwiftFormer `dist_in1k` → ImageNet. | **3** — `["NSFL","NSFW","SFW"]` (read from `config.json`). | **Ships ONNX fp32 + fp16 with preprocessing baked into the graph.** The best packaging in the table. | 23.7 MB ONNX (s); 13.1 MB (xs). | **Reject.** Three classes, but the *wrong* three: NSFL is gore. There is no medical-or-artistic axis, which is the entire reason issue #21 asked for three. |
| 5 | **AdamCodd/vit-base-nsfw-detector** | `license: apache-2.0` in HF card frontmatter. | **No.** No `LICENSE` file. | "~25,000 images." No source, no rights. Card admits accuracy drops to 0.86 on generative images. Base `google/vit-base-patch16-384` → ImageNet. | **2** — SFW/NSFW. | Nine pre-built ONNX variants, fp32 through q4f16. | 344.6 MB fp32 / 50.3 MB q4f16. | **Reject.** Binary; smallest and least documented training set of the group. |
| 6 | **GantMan/nsfw_model** | **MIT** — [LICENSE.md](https://github.com/GantMan/nsfw_model/blob/master/LICENSE.md). | **Yes — read in full.** | **The clearest disqualification in the table, and it is disqualifying because it is documented.** "Trained on 60+ Gigs of data," credited to `nsfw_data_scraper`, which uses Ripme to bulk-download images from "hundreds of subreddits" into five categories: porn, hentai, sexy, neutral, drawings. Bulk-ripped pornography from Reddit. No consent, no rights, no takedown path. | **5** — drawings / hentai / neutral / porn / sexy. | Keras InceptionV3 / MobileNetV2 → ONNX; conversion plausible, not attempted. | ~100 MB class. | **Reject.** Five classes is closer to what we want than anything else off the shelf, and it does not matter: we cannot build a safety claim for a family-photo product on a corpus of scraped Reddit pornography. |
| 7 | **Bumble private-detector** | **Apache-2.0**, "Copyright 2022 Bumble" — [LICENSE](https://github.com/bumble-tech/private-detector/blob/main/LICENSE). | **Yes — read in full.** | "our internal dataset of lewd images" — i.e. **images real people sent each other in private chats on a dating app.** Not released, size not stated, no consent statement, no rights statement. §4.4. | **1** — a lewd probability. Binary. | TF SavedModel + checkpoint + frozen graph → `tf2onnx`. Plausible, not attempted. | EfficientNet-v2 class, ~100 MB. | **Reject.** Cleanest licence of any third-party candidate and the worst provenance story. Binary. Last pushed 2023-11-05. |
| 8 | **Freepik/nsfw_image_detector** | `license: mit` in HF card frontmatter; the companion GitHub repo carries a real **Apache-2.0** [LICENSE](https://github.com/freepik-company/nsfw_image_detector/blob/main/LICENSE). Two different licences for code and weights, neither contradicting the other, both permissive. | **Partly.** Read the Apache-2.0 file (code). The MIT on the weights is a card tag only; no `LICENSE` file beside the weights. | "100,000 synthetically labeled images." Deduplicated against validation with CLIP at cosine 0.92. **"Synthetically labeled" is never defined and the image source is never stated.** Base `eva02_base_patch14_448.mim_in22k_ft_in22k_in1k` → ImageNet-22k/1k. | **4** — ordinal `neutral / low / medium / high` (read from `config.json`). | timm EVA-02 → ONNX; plausible. | 172.7 MB, and **448×448 input** — a second full vision tower on every photo. | **Reject.** The ordinal severity axis is the most useful shape on offer, but it is a severity ladder, not a *context* axis: `medium` cannot distinguish a bikini from a mastectomy scar, which is the exact distinction issue #21 exists for. |
| 9 | **prithivMLmods SigLIP 2 heads** (`siglip2-mini-explicit-content`, `Guard-Against-Unsafe-Content-Siglip2`) | `license: apache-2.0` in HF card frontmatter. | **No.** No `LICENSE` file. | Declares `strangerguardhf/NSFW-MultiDomain-Classification`, tagged `license: apache-2.0`, 10 K–100 K images, tags include `Hentai`, `Pornography`, `synthetic`, `not-for-all-audiences`. **An individual uploader tagging Apache-2.0 on a corpus of scraped pornography is a metadata field, not a licence grant** — they do not hold the rights they are purporting to license. §4.3. | **5** — anime-picture / hentai / normal / pornography / enticing-or-sensual. | SigLIP 2 → ONNX routine. | ~370 MB. | **Reject.** Also architecturally wrong for us: these are **full fine-tunes of `siglip2-base-patch16-256/512`**, a different embedding space from the `so400m-384` (1152-d) tower we already run — so they cost a second full forward pass, not a free head. |
| 10 | **NudeNet** (notAI-tech) | **AGPL-3.0** — reported by the GitHub API licence field. | Metadata only (did not read the file; the SPDX identifier is unambiguous). | n/a | Detection boxes over ~18 body-part classes. | ONNX shipped. | ~30 MB. | **Reject — hard.** AGPL is incompatible with shipping a proprietary desktop app. Identical rule to Essentia in `docs/model-registry.md`. Actively maintained (last push 2026-06-09), which makes no difference. |
| 11 | **ShieldGemma 2 (4B)** | **Gemma Terms of Use** — [ai.google.dev/gemma/terms](https://ai.google.dev/gemma/terms). Commercial use permitted *subject to* the Prohibited Use Policy; Google reserves the right to remotely restrict usage; terms flow through to every derivative and to models distilled from it. | Read the summary terms, not clause by clause. | Curated natural + synthetic images, "a subset of WebLI relevant to safety tasks." Better documented than any other third-party candidate. | **3** — Sexually Explicit / Dangerous Content / Violence & Gore. | Possible, pointless. | **4 B parameters.** | **Reject on two independent grounds.** (a) Field-of-use restrictions plus a unilateral remote-restriction right is not a permissive licence by this project's standard — same family as the licences already rejected. (b) 4 B params per photo violates criterion 5; a gate that cannot finish returns `indeterminate` and blocks publication. Its three classes are also not our three. |
| 12 | **Apple SensitiveContentAnalysis** (macOS 14+) | Apple SDK / Developer Program License Agreement. No open-source licence; no redistribution; entitlement-gated. | Terms summarised from Apple's own docs; DPLA not read clause by clause. | **Entirely undisclosed.** Apple publishes nothing about the model or its training data. | **1 + beta** — `isSensitive: Bool`, plus `detectedTypes: Set<ContentType>` which is **beta API with undocumented values**. Also three intervention-hint booleans. No scores. | n/a — platform API, not a model artifact. | n/a | **Reject as the gate. Full assessment in §5** — the disqualifier is not the licence, it is that the framework is **off by default and the app cannot turn it on.** |
| 13 | ✅ **SigLIP 2 `so400m-384` + our own 1152→3 linear head** | Base weights `license: apache-2.0` on Google's own HF org — [model card](https://huggingface.co/google/siglip2-so400m-patch14-384). Head weights: ours, no third-party licence at all. | **No `LICENSE` file** in the HF repo — Apache-2.0 is a card tag. It is however a **first-party** tag from the organisation that did the training, which is a materially stronger position than a third party tagging weights derived from someone else's data. | Base: **WebLI**, already assessed and accepted in `models/configs/siglip2-so400m-384.json` and `docs/model-registry.md`. Head: **zero third-party NSFW images** in the recommended construction (§6). Provenance of the head is our own prompt bank and our own documented calibration corpus. | **3** — exactly `explicit` / `suggestive` / `medical_or_artistic`, by construction. | Trivial. A `Gemm` + `Sigmoid`. Runs anywhere. | **≈13.5 KiB** (1152×3 + 3 = 3 459 fp32 params). Zero marginal inference cost — the embedding already exists. | **RECOMMENDED.** |

---

## 3. The licence-file distinction, stated plainly

The task asked me to distinguish "I read the licence file" from "the card says permissive but I could not find the actual licence." The result is stark enough to be worth its own list.

**Licence file read in full (4):**

- LAION CLIP-based-NSFW-Detector — `license.md`, verbatim MIT, Copyright 2022 Christoph Schuhmann.
- GantMan/nsfw_model — `LICENSE.md`, MIT.
- Bumble private-detector — `LICENSE`, Apache-2.0, Copyright 2022 Bumble.
- freepik-company/nsfw_image_detector (the **code** repo) — `LICENSE`, Apache-2.0.

**Card metadata tag only, no licence file exists in the repository (7):**

Falconsai, Marqo, OwenElliott (s and xs), AdamCodd, prithivMLmods, Freepik's **weights**, and — for completeness and against my own recommendation — `google/siglip2-so400m-patch14-384` itself.

I confirmed the absence by listing every file in each repository through the HF API, not by failing to find one. For all seven, the entire licence grant is a string in YAML frontmatter that the uploader typed.

Two observations that follow:

- **This is the norm on Hugging Face, and it is weak.** A frontmatter tag is a statement of intent by whoever pushed the repo. It is not a signed grant, it carries no copyright notice, no attribution requirement text, and it can be edited in a commit with no history anyone diffs. `docs/model-registry.md` already warns that permissive *code* does not imply permissive *weights*; this is the next layer of the same trap — a permissive *tag* does not imply a permissive *grant*.
- **Not all tags are equal.** `google/siglip2-*` being tagged Apache-2.0 by Google, on weights Google trained on data Google collected, published alongside `google-research/big_vision` under Apache-2.0, is a first-party statement by the only party who could grant anything. `prithivMLmods` tagging Apache-2.0 on a head trained on `strangerguardhf`'s scraped pornography is a third party purporting to license something two levels removed from anything they own. Both are "card says permissive"; they are not the same finding, and flattening them would be exactly the plausible-looking wrong answer this project keeps having to undo.

---

## 4. Provenance findings

This is where the decision was actually made. Every third-party NSFW classifier examined fails here, and they fail in five distinguishable ways.

### 4.1 LAION — the training corpus was withdrawn after CSAM was found in it

The detector's own repository does not document its training data: it offers a Google Drive download of CLIP embeddings and states they are "not fully manually annotated." No image source, no count, no annotators, no terms.

Separately and verifiably: **LAION took LAION-5B down on 19.12.2023** after the Stanford Internet Observatory reported suspected CSAM in it. LAION's own Re-LAION-5B announcement states that **2 236 links were removed**, subsuming the **1 008 links** the SIO report identified as "CSAM" or "likely CSAM." Re-LAION-5B Research-Safe additionally drops 3.044 % of samples as NSFW.

**I could not establish that the NSFW detector was trained on LAION-5B or LAION-400M.** The repo does not say. The inference example operates on LAION5B and the organisation is the same, which is suggestive and nothing more. I am recording it as unestablished rather than assuming it.

But the honest framing does not need that link to be proven. It is this: **the training corpus of the most widely copied open NSFW head is undocumented, and it comes from an organisation whose flagship corpus was withdrawn after CSAM was found in it.** For a product whose gate exists to protect children's photographs, "we don't know what's in it, and the people who made it have previously not known what was in theirs" is a sufficient answer. Undocumented is itself the finding.

### 4.2 Marqo, Falconsai, AdamCodd, OwenElliott — proprietary and undocumented, and one of them names its source

Four repositories, four variants of the same sentence: "a proprietary dataset of N images." None states where the images came from, who labelled them, under what terms, or whether any rights were obtained.

Two of them do volunteer one detail: Marqo's card lists **"Rule 34 material"** as a content type, and OwenElliott's lists **"Rule 34"** among its web sources. Rule 34 sites are aggregators of user-uploaded pornographic artwork, frequently depicting copyrighted characters, with no rights clearance of any kind. Naming it is more honest than most cards manage — and it is a named category of scraped adult material with no rights position, which is precisely the disqualifier.

OwenElliott compounds it: its labels were **validated using Marqo's detector**, so it inherits both Marqo's provenance and Marqo's decision boundary. Two models, one corpus, one opinion about what "NSFW" means.

### 4.3 strangerguardhf — an Apache-2.0 tag over material the uploader cannot license

`strangerguardhf/NSFW-MultiDomain-Classification` is tagged `license: apache-2.0` and carries tags `Hentai`, `Pornography`, `synthetic`, `not-for-all-audiences`. An individual applying Apache-2.0 to a scraped pornography corpus is asserting a grant over copyrights they do not hold. **This tag reduces risk by exactly zero**, and models trained on it (the prithivMLmods SigLIP 2 heads) inherit the position, whatever their own card says.

This is worth naming as a general pattern, not a one-off: on Hugging Face, dataset licence tags on scraped image corpora are almost always the uploader's preference for their *collection effort*, not a grant over the underlying works. Treating them as a licence is a category error.

### 4.4 Bumble — the cleanest licence and the worst consent story

Apache-2.0, verbatim, read in full, Copyright 2022 Bumble. And the model was trained on "our internal dataset of lewd images": images real people sent to each other in private chats on a dating app. There is no statement that those users consented to their intimate images becoming training data, and there is no way for a downstream user to check, because the dataset is not released.

This one is instructive because it inverts the usual heuristic. Everything a licence audit looks at is green. Every question a *provenance* audit asks comes back worse than for the scraped models — because scraped web images were at least published by their subjects, and these were not.

### 4.5 ImageNet pretraining touches almost every candidate

ImageNet's terms of access state, verbatim: **"Researcher shall use the Database only for non-commercial research and educational purposes."** The agreement further binds a researcher's for-profit employer to the same terms.

Every third-party candidate in the table except ShieldGemma 2 and the SigLIP 2 route is fine-tuned from an ImageNet-pretrained backbone: Falconsai (`vit-base-patch16-224-in21k`), Marqo (`augreg_in21k_ft_in1k`), AdamCodd (`vit-base-patch16-384`), OwenElliott (SwiftFormer `dist_in1k`), Freepik (`eva02 ... in22k_ft_in22k_in1k`), GantMan (InceptionV3/MobileNetV2 ImageNet), Bumble (EfficientNet-v2 ImageNet).

**Whether that non-commercial clause reaches trained weights is genuinely unsettled** and I am not going to pretend otherwise — see §8. I raise it because it is a systemic exposure that a per-model licence audit misses entirely, and because it points the same direction as everything else: the SigLIP 2 route, whose base was trained by Google on Google's own WebLI and released Apache-2.0 by Google, has the shortest and cleanest chain available.

### 4.6 One specific landmine worth flagging: Falconsai ships a YOLOv9-derived file

`Falconsai/nsfw_image_detection` — the most-downloaded NSFW classifier on Hugging Face by a wide margin — contains, alongside its ViT weights, a file named `falconsai_yolov9_nsfw_model_quantized.pt` (87.1 MB). The YOLOv9 reference implementation is **GPL-3.0**. The repository is tagged `apache-2.0` with no `LICENSE` file and no NOTICE explaining the discrepancy.

I have **not** verified that this file is in fact a YOLOv9 derivative — I read its filename and its size, nothing more. I am flagging it rather than concluding it. But it is exactly the shape of defect that ends up in a shipped product: the top result, an Apache-2.0 tag, and a GPL-derived artefact sitting in the same directory that nobody diffed.

### 4.7 What this all adds up to

There is no off-the-shelf open NSFW classifier with documented, rights-cleared training data. Not one. The best-documented is ShieldGemma 2, and its licence disqualifies it. **The provenance requirement in issue #21 is not a filter that narrows the field — it empties it.** That is why the recommendation is a construction rather than a download.

---

## 5. Apple Sensitive Content Analysis — full assessment

The brief asked for this to be assessed properly rather than dismissed, and for the macOS-first build it *is* plausible on its face: Apple-quality model, genuinely local, zero weights to license, zero MB to ship. It fails, but not for the reason one expects, and the reason is worth writing down so nobody re-proposes it.

### 5.1 Licence terms

There is no open-source licence. Use is governed by the Apple SDK licence and the Apple Developer Program License Agreement. Apple's own article states: *"Any team member of the paid App Store developer program can add the entitlement to an app after enabling the capability in Xcode and then signing the Developer Program License Agreement... The Sensitive Content Analysis entitlement isn't available for Enterprise development or for people with free accounts."*

Practically: no redistribution question, no weights to audit, but also **nothing to version-pin and nothing to put in the model registry.** For a registry whose stated purpose is that "a model that is not in this table cannot be loaded," an unversioned OS service is a category the registry cannot represent.

### 5.2 Reachable from a Rust or Python host? Yes — and this is not the problem

Both bindings exist and I confirmed both:

- **Rust:** `objc2-sensitive-content-analysis` v0.3.2 on crates.io, "Bindings to the SensitiveContentAnalysis framework."
- **Python:** `pyobjc-framework-SensitiveContentAnalysis` v12.2.2 on PyPI, MIT.

So `workers/ml-runtime` (Python) or the Tauri Rust core could both call it. Reachability is genuinely fine.

What is *not* fine is that the entitlement lives in the **code signature of the running process**. An unsigned dev-mode Python host cannot obtain it. A real report against the PyObjC bindings (ronaldoussoren/pyobjc#654) shows exactly what that looks like from Python: error code 100 from the framework with the message **"User Safety either not entitled for client or not enabled."** So the development and CI story is: it does not work at all until the whole host is signed and entitled, and it cannot be exercised in the iOS Simulator either.

### 5.3 What it actually returns

`SCSensitivityAnalysis` exposes:

- `isSensitive: Bool` — that is the entire determinate output.
- `detectedTypes: Set<SCSensitivityAnalysis.ContentType>` — **beta API**, and the documentation page does not enumerate the values.
- `shouldIndicateSensitivity`, `shouldInterruptVideo`, `shouldMuteAudio` — UI intervention hints.

**There is no score.** `SafetyClearance.ItemVerdict` requires `ClassScores` — three `Unit` probabilities — on every `cleared` or `blocked` verdict, and the schema's stated reason is that recorded scores make a verdict "re-auditable against a changed threshold without re-running inference." A boolean cannot be re-audited against anything. And the 0.3-not-0.5 decision, described in the config as "the one number in this file that is a policy decision," has nothing to apply to: Apple's threshold is Apple's, unstated, and not ours to move.

### 5.4 The disqualifier: it is off by default and the app cannot turn it on

From Apple's own article, verbatim — the framework returns `disabled` when:

> - The app lacks the necessary `com.apple.developer.sensitivecontentanalysis.client` entitlement.
> - Neither the Sensitive Content Warning user preference nor the Communication Safety setting in Screen Time are active.
> - One of the sensitive content detection settings is active, but the person turned off sensitive-content warnings for your app in Settings.

And *"This class successfully detects sensitive content only when `analysisPolicy` is a value other than `disabled`."*

Sensitive Content Warning is **off by default** on macOS; the user turns it on at System Settings → Privacy & Security → Sensitive Content Warning, and then chooses which apps may use it. Communication Safety is a Screen Time parental control, i.e. a child's device.

Now run that against Codex's mechanism. `disabled` means we get no verdict. No verdict is `indeterminate`. `indeterminate` **blocks**, cannot be overridden, and one such item denies the whole publication. So for every user who has not gone and enabled an unrelated OS-wide setting — the overwhelming majority — **every album, every reel and every contact sheet would be permanently blocked.** Not degraded. Blocked.

The alternative is to treat "policy disabled" as a pass, which is the fail-open defect the schema was written to prevent and which this project has already shipped once (#18, the load gate that permitted unhashed weights).

There is no third option. That is the whole argument.

### 5.5 The local-first question, and the rule 7 question

**Local-first:** using SCA is not an egress. Apple's framework documentation instructs developers not to transmit results (*"don't transmit any information off the person's device about whether the Sensitive Content Analysis framework identified an image or video as containing sensitive content"*), which is an obligation on us, not a claim about Apple. It also states the framework is *"to prevent people from viewing unwanted content, not as a way for an app to report on someone's behavior"* — a purpose limitation that a publication gate arguably sits outside of. **I could not retrieve a verbatim first-party sentence stating that all analysis runs on device** (§8). So: probably not an egress, plausibly a purpose mismatch, and not something I can assert as clean.

**Rule 7 — "no silent model swap":** this is the objection the brief anticipated, and it is real but secondary. Apple can change the model in any point release. There is no version to pin in `ClassifierPin.model`, no digest for `SafetyClearance`, no way to run it through the eval harness in CI, and no way to know a swap happened. Two consequences beyond the rule itself: a photo cleared on macOS 14 might be blocked on macOS 15 with no record of why, and clearance would become **machine-dependent**, which sits badly against rule 3's determinism promise even though the manifest keeps the *render* deterministic.

### 5.6 Cross-platform

Windows and Linux have no equivalent. `models/configs/nsfw-siglip-head.json` lists `onnxruntime_cuda` and `onnxruntime_directml` targets. Adopting SCA on macOS means building the real classifier anyway for everyone else — and then shipping two gates with different, unmeasurable decision boundaries, which is worse than one gate.

### 5.7 Verdict on Apple SCA

**Do not use it as the publication gate.** Off by default, unenableable by us, boolean-only, unpinnable, uneval-gatable, macOS-only.

**Two narrower uses remain legitimate and are worth keeping on the table:**

1. **As a strictly one-directional additional blocker.** If `analysisPolicy != .disabled` and `isSensitive == true`, force `blocked`. It can only ever *add* blocks, never clear anything, so it cannot weaken the safety claim. Cost: verdicts become machine- and OS-dependent, and `SafetyClearance` has no field to record the OS build that produced them — so this needs a contracts change and Codex's sign-off, not a quiet implementation. I would defer it past v1.
2. **In the review UI.** Blurring a flagged thumbnail in the user's own review queue on a Mac where they have already asked the OS for that behaviour is exactly what Apple built the framework for. Default-off is correct there, boolean is sufficient there, and nothing irreversible depends on it. This is a genuinely good fit and belongs in `apps/desktop/` (Codex's territory) as a UX feature, entirely separate from issue #21.

---

## 6. Recommendation

**Ship a 1152→3 linear head over the SigLIP 2 `so400m-384` embedding we already compute. Initialise it from SigLIP 2's own text tower. Calibrate it on a small, internally assembled, provenance-documented corpus. Ship no third-party NSFW weights and no third-party NSFW images, ever.**

The placeholder config was right about the shape. It was right about the input (`image_embeds`), right about the output (`[-1, 3]` + sigmoid), right about the cost argument, and right to block itself until a human chose. What follows fills in the model.

### 6.1 Why this and not "pick the least-bad download"

Because §4 empties the field, and because of an asymmetry worth stating: for most models in this stack, undocumented training data is a *quality* risk. For this one it is the product. The classifier's entire job is to make a safety claim about a family's private photographs, including their children's. A claim founded on 220 000 images of unknown origin scraped from Rule 34 and Reddit is not a weaker version of the right answer, it is a different kind of thing.

There is also a plain commercial fact: this ships. If anyone ever asks "what is your nudity detector trained on?" — a reviewer, a print partner, an enterprise buyer, a journalist, a regulator under the EU AI Act's transparency obligations — **"we don't know"** is the answer for every candidate in the table except this one.

### 6.2 The construction, in three steps

**Step 1 — zero-shot initialisation (no NSFW training images at all).**

SigLIP 2 ships a text tower. Offline, once, at build time, embed a curated prompt bank of 20–40 natural-language prompts per class through `google/siglip2-so400m-patch14-384`'s text encoder, average within class, L2-normalise, and stack. That is a 1152×K matrix, which *is* a linear head. Ship the matrix; the text tower never enters the product.

K = 4, not 3: `explicit`, `suggestive`, `medical_or_artistic`, and a `benign` reference direction used to normalise the other three. Three scores are emitted, matching the contract.

Licence chain: Google's Apache-2.0 weights → a matrix we computed. Provenance chain: **WebLI, and nothing else** — the same corpus already assessed and accepted for the embedding model, and the only image corpus in this decision whose terms the project has already reviewed. We add zero new third-party provenance. The prompt bank itself is text we write, and it goes in the repo where it can be reviewed, diffed and argued with — which is more scrutiny than any candidate's training set permits.

**Step 2 — calibration, which is 6 numbers, not 3 459.**

Zero-shot SigLIP logits are not probabilities. Fit a per-class Platt scaling — one temperature and one bias per class, six parameters total — so the sigmoid output is a calibrated probability and the decided 0.3 threshold means something. Six parameters need a few hundred labelled examples per class, not tens of thousands. **This is the entire reason to prefer zero-shot initialisation over training a head: it moves the data requirement from "a corpus we cannot legally or ethically obtain" to "an evaluation set we can."**

**Step 3 — full refit, only if step 2 fails its gate.**

If §7's go/no-go fails, fit the full 1152×3 logistic regression on the same corpus. This is the fallback, and it is a fallback precisely because it reintroduces the data-volume problem in full.

### 6.3 The calibration corpus, and the one part of it we cannot source

Every image gets a recorded source URL, licence and collection date, and the manifest is committed. Non-negotiable — this corpus *is* the provenance claim.

| Class | Source | Feasible? |
|---|---|---|
| benign / family | Our own and consented volunteer dev libraries; CC-BY / CC0 people-photography from Wikimedia Commons and Openverse. | Yes. |
| `suggestive` | CC-licensed swimwear, beach, sports and lingerie imagery from Wikimedia Commons and Openverse. | Yes. |
| `medical_or_artistic` | Wikimedia Commons has substantial CC-BY / CC-BY-SA / PD material for breastfeeding, post-mastectomy and post-surgical documentation, dermatology, and life drawing. Museum open-access programmes (Met, Rijksmuseum, Art Institute of Chicago) provide CC0 fine-art nudes at high resolution. | Yes, and better than any commercial dataset offers. |
| `explicit` | — | **No. See below.** |

**We cannot source photographic explicit imagery with clean provenance, and I am not going to write a plan that pretends we can.** There is no permissively licensed, model-released explicit corpus available to a company this size. Three honest options:

- **(a) Interim, no cost.** Hold no explicit photographic imagery. Validate the `explicit` head only against the strongest legitimately obtainable boundary material — explicit fine-art nudes, PD erotic art (shunga, classical) — and **record measured explicit recall on photographic pornography as unknown.** Unknown, in the eval report, as a gap. Not as a pass.
- **(b) Recommended before commercial release.** Commission a licensed evaluation set from a content-moderation vendor under a written DPA, with an explicit warranty of rights and lawful sourcing. Costs money; it is the only route that ends with a defensible number. Business decision, same category as the Essentia commercial licence.
- **(c) Rejected.** Measure agreement with a third-party model instead of ground truth. This measures whether we reproduce Marqo's opinion, launders their provenance into our eval report, and produces a number that looks like accuracy and is not.

**Ship (a), plan (b), never (c).**

### 6.4 The line on third-party models during development

Third-party NSFW models (Marqo, OwenElliott) may be used **offline, inside `packages/eval-harness/` only**, for two things: measuring rank agreement as a sanity check, and mining candidate hard cases within our own consented libraries for a human to then label.

They may **never** be a label source for the shipped head, because that launders their provenance into ours and makes our model a distillation of a corpus we rejected. Their weights never enter `models/registry.json` and never ship. This line should be written into the model card, because it is the kind of line that erodes quietly.

### 6.5 Class semantics and thresholds

The three classes are **independent sigmoids, not a softmax.** A breastfeeding photograph should legitimately score high on `medical_or_artistic` and non-trivially on `suggestive` at the same time; forcing them to compete for probability mass is a rebuild of the one-flag failure the issue exists to avoid.

Proposed gate policy:

- `explicit ≥ 0.3` → `blocked`. Human-overridable per item and per sink.
- `suggestive ≥ 0.3` → `blocked`. Human-overridable.
- `medical_or_artistic` is **not** a blocking threshold. It is a **disposition** signal that changes the remedy: when it is high *and* a blocking class fired, the item goes to the review queue with a "this may be a medical or artistic photograph" prompt rather than being silently omitted.

**That last bullet goes beyond what issue #21 decided and needs sign-off before it is implemented.** The issue fixed the threshold at 0.3 and fixed three classes; it did not say what the third class *does*. I am proposing it because a class that only ever contributes to blocking is a one-flag model with extra columns — the parent whose breastfeeding photo was dropped needs to be *told*, not just given an override button they will never find. But it is a policy change, so it is a proposal, not a decision.

### 6.6 Registry and config changes this implies

1. **Pin the class order in the config.** `outputs[0]` is `sensitive_logits` with shape `[-1, 3]` and there is currently **nothing anywhere that says which index is which class.** An unlabelled 3-vector crossing the ml-runtime boundary is precisely the silent-swap hazard rule 7 exists for: transpose two columns and every breastfeeding photo becomes explicit, with no test failing. Add an explicit `class_order: ["explicit", "suggestive", "medical_or_artistic"]` and assert it in the golden fixture. **This is a real defect in the placeholder and should be fixed whichever model wins.**
2. **Rename `model_id`.** `nsfw-siglip-head` describes neither what it does nor what it emits; `sensitive-content-siglip2-head` does. Renaming a registry id is cross-boundary (ml-runtime loads by id) — propose to Codex, do not do unilaterally.
3. **Keep `blocks_commercial_release: true`** until §7 passes *and* `license.verified: true` with a date and a human's name.
4. **Record the licence position honestly in the model card**: base Apache-2.0 by card tag, no LICENSE file, first-party; head weights ours; calibration corpus manifest committed at a named path.
5. **`weights.blake3` must be filled** before the head can load in release mode — the load gate already enforces this after #18, and it is the mechanism that keeps a hand-swapped head out.

---

## 7. Eval gate — the go/no-go this recommendation is conditional on

This design is unmeasured. It gets promoted out of `placeholder` only by passing a gate in `packages/eval-harness/`, and the gate must be defined before the head is fitted so it cannot be defined around the result.

- **Slices, reported separately, never averaged:** infants and toddlers (bath, nappy, beach); breastfeeding; post-surgical and dermatological; fine-art nudes and museum photography; swimwear and beach holidays; sports; cultural and religious dress; **skin tone** (Monk scale bands); drawings and AI-generated imagery.
- **Primary metric:** recall on `explicit` at the operating point, with the §6.3 caveat that this is measurable only on boundary material until (b) happens.
- **Secondary:** false-positive rate on the infant slice and the breastfeeding slice. These will be the top two support complaints and they should be tracked as product metrics from day one, not discovered later.
- **Bias gate:** no slice's false-positive rate may exceed the best slice's by more than a fixed factor. India-first product, web-trained backbone — see §9.
- **Failure of the gate promotes nothing.** The config stays `placeholder`, `blocks_commercial_release` stays `true`, publication stays blocked. A blocked product is the correct state for a product whose safety gate does not work.

---

## 8. Honest failure modes of the recommendation

Which photographs this will get wrong, and in which direction. These are predictions from how CLIP-family embeddings behave, **not measurements** — nothing has been run.

### 8.1 Errs toward blocking — cost: one photo omitted, user can override

- **Infants and toddlers: bath time, nappies, paddling pools, the beach.** Skin fraction dominates the embedding and the semantics do not save it. This is simultaneously the most common category in a real family library and the most likely to over-trigger. **Expect this to be the single largest source of complaints**, and expect it to feel worst precisely to the users who care most — new parents, who are the core customer.
- **Breastfeeding.** Will score on `suggestive` and `medical_or_artistic` together. Under §6.5 this routes to review rather than silent omission — which is the entire justification for the third class, and if it does not work in practice the three-class design has failed at its headline case.
- **Post-mastectomy, surgical scars, dermatology, physiotherapy, pregnancy bump photography.** Over-blocked. These are often the most emotionally load-bearing images in a library, and quietly dropping them from an anniversary album is a real harm even though it is the "safe" direction.
- **Museum and holiday art.** A photo of a statue in the Louvre or a temple carving at Khajuraho. Frequent in travel libraries.
- **Sport and beach.** Swimming galas, athletics, gymnastics, wrestling, saunas, spa days.
- **Holi, colour runs, body paint, tattoos, life drawing, and cultural dress that exposes the torso.**

### 8.2 Errs toward passing — cost: irreversible

- **Small-in-frame or background explicit content, and this one is structural, not incidental.** The gate scores the **low-resolution proxy** — the same proxy used to build contact sheets, deliberately low-res for privacy. Low resolution is exactly where small-in-frame content stops being legible to the embedding. **The privacy design and the safety design pull against each other at the single sharpest boundary in the system, the one where content leaves the device.** I do not have a clean answer. Options are to run the gate at a higher resolution than the contact sheet for the `frontier_egress` sink only (costs a second embedding pass), or to accept the gap and record it. This deserves its own discussion with Codex and it should not be buried in an implementation.
- **Drawings, anime, hentai, AI-generated explicit content.** Our prompt bank describes photographic concepts; SigLIP handles illustrated content unevenly. **Every third-party model in §2 trained specifically on Rule 34 and hentai — because they had to.** Our head, trained on nothing, will be weakest exactly where they invested most. Honest expectation: substantially worse than Marqo on illustrated explicit content.
- **Sexting and self-shot imagery received over WhatsApp.** `AGENTS.md` names WhatsApp folder conventions as a first-class ingest source. In a real user's library this is plausibly the *highest-prevalence* explicit content, and it is close-range, low-light, phone-camera, off-centre — poorly represented in web-caption training data. A likely miss, in the category that matters most.
- **Screenshots of explicit content.** Different image statistics; UI chrome dominates. Partly mitigated because PaddleOCR screenshot detection already excludes screenshots from memories — but that is a different gate for a different reason, and relying on it is coupling we should name.
- **Video.** The gate runs on sampled frames. A clip that is benign at every sampled keyframe and explicit between them clears. **The frame-sampling policy is part of the gate's decision boundary, not an implementation detail**, and it must be pinned in the config and recorded in `ClassifierPin`, or two runs of the same clip can legitimately disagree.

### 8.3 The bias failure, stated separately because it will not show up in an average

Web-scraped-corpus classifiers are repeatedly documented to over-flag darker skin and non-Western dress. This is an India-first product. A gate that quietly drops more photographs from a darker-skinned family's album than a lighter-skinned one is a discrimination failure that a single accuracy number will never reveal — it will look like a 2 % false-positive rate. Hence the per-slice reporting and the explicit bias gate in §7. Choosing SigLIP 2 over Reddit-scraped corpora probably helps here, and **probably is not a measurement.**

### 8.4 Scope notes

- **This is not a CSAM detector and must never be described as one, internally or externally.** CSAM detection is a legal-reporting domain (hash matching against NCMEC lists, mandatory reporting) that this product does not enter, and assembling training data for it is unlawful. Someone will ask; the answer is written down here.
- **Adversarial robustness is out of scope.** The only party who could adversarially perturb an image to defeat this gate is the library's owner, acting against their own library. Not a threat model. Ordinary JPEG compression, resizing and colour shifts *are* in scope and belong in the eval.

---

## 9. What I could not establish

Listed rather than guessed. Every one of these is a place where a plausible-sounding sentence would have been easy to write.

1. **Whether the LAION NSFW head was trained on LAION-400M or LAION-5B.** The repository does not say. The organisational link and the LAION5B inference example are suggestive; that is not evidence.
2. **Whether Marqo, Falconsai, AdamCodd, OwenElliott or Bumble obtained any rights or consent for their training images.** None publishes anything. This is not an absence of information I failed to find — I read every card and every repository file listing. Undocumented is the finding.
3. **Whether ImageNet's non-commercial research clause binds downstream commercial use of ImageNet-pretrained weights.** I read the clause verbatim. Its application to trained weights is legally unsettled and I found no authoritative resolution. It affects every third-party candidate except ShieldGemma 2 and the SigLIP 2 route.
4. **Whether `com.apple.developer.sensitivecontentanalysis.client` is provisionable for a Developer ID-signed macOS app distributed outside the Mac App Store.** Apple's entitlement page lists availability as iOS 17.0+ / iPadOS 17.0+ only, while the framework itself is macOS 14.0+. I found no authoritative statement either way. Moot given §5.4, but unresolved.
5. **Whether Apple's SCA analysis runs entirely on device.** I could not retrieve a verbatim first-party sentence saying so. Apple's developer docs impose a *developer* obligation not to transmit results, and the marketing describes it as privacy-preserving; neither is the same claim. The PyObjC error trace suggests a local "User Safety" service, which is suggestive and not proof.
6. **What `SCSensitivityAnalysis.ContentType` enumerates.** Beta API; values not on the page I fetched. So I cannot say whether Apple's categories could ever map to our three, even in principle.
7. **Whether the Falconsai `.pt` file is genuinely YOLOv9-derived.** Filename and size only. Flagged in §4.6, not concluded.
8. **Whether the LAION AutoKeras model converts cleanly to ONNX.** Not attempted.
9. **Any accuracy figure whatsoever for the recommended head.** Nothing was run. §7 exists to fix this and the recommendation is conditional on it.
10. **Whether zero-shot SigLIP 2 is good enough at `explicit` to pass §7 at all.** This is the load-bearing unknown in the entire recommendation. Note the uncomfortable prior: LAION built a trained head rather than using zero-shot CLIP, which suggests zero-shot was not sufficient for them in 2022. SigLIP 2 in 2026 is a much stronger encoder and the task is easier than theirs (we gate rather than filter a 5-billion-image corpus), but **if step 1 fails, §6.2 step 3 needs training data we do not have a clean source for, and the decision reopens.** That is the honest risk in this document and it should be tested early, not late.

---

## Sources

- [LAION-AI/CLIP-based-NSFW-Detector — license.md](https://github.com/LAION-AI/CLIP-based-NSFW-Detector/blob/main/license.md) · [README](https://github.com/LAION-AI/CLIP-based-NSFW-Detector)
- [LAION — Releasing Re-LAION-5B](https://laion.ai/blog/relaion-5b/)
- [Falconsai/nsfw_image_detection](https://huggingface.co/Falconsai/nsfw_image_detection)
- [Marqo/nsfw-image-detection-384](https://huggingface.co/Marqo/nsfw-image-detection-384)
- [OwenElliott/image-safety-classifier-s](https://huggingface.co/OwenElliott/image-safety-classifier-s)
- [AdamCodd/vit-base-nsfw-detector](https://huggingface.co/AdamCodd/vit-base-nsfw-detector)
- [GantMan/nsfw_model — LICENSE.md](https://github.com/GantMan/nsfw_model/blob/master/LICENSE.md) · [README](https://github.com/GantMan/nsfw_model) · [alex000kim/nsfw_data_scraper](https://github.com/alex000kim/nsfw_data_scraper)
- [bumble-tech/private-detector — LICENSE](https://github.com/bumble-tech/private-detector/blob/main/LICENSE) · [README](https://github.com/bumble-tech/private-detector)
- [Freepik/nsfw_image_detector](https://huggingface.co/Freepik/nsfw_image_detector) · [freepik-company/nsfw_image_detector — LICENSE](https://github.com/freepik-company/nsfw_image_detector/blob/main/LICENSE)
- [prithivMLmods/siglip2-mini-explicit-content](https://huggingface.co/prithivMLmods/siglip2-mini-explicit-content) · [strangerguardhf/NSFW-MultiDomain-Classification](https://huggingface.co/datasets/strangerguardhf/NSFW-MultiDomain-Classification)
- [notAI-tech/NudeNet](https://github.com/notAI-tech/NudeNet)
- [google/shieldgemma-2-4b-it](https://huggingface.co/google/shieldgemma-2-4b-it) · [Gemma Terms of Use](https://ai.google.dev/gemma/terms) · [ShieldGemma 2 paper](https://arxiv.org/abs/2504.01081)
- [google/siglip2-so400m-patch14-384](https://huggingface.co/google/siglip2-so400m-patch14-384)
- [ImageNet — Download / Terms of Access](https://www.image-net.org/download.php)
- Apple: [Sensitive Content Analysis](https://developer.apple.com/documentation/sensitivecontentanalysis) · [SCSensitivityAnalyzer](https://developer.apple.com/documentation/sensitivecontentanalysis/scsensitivityanalyzer) · [SCSensitivityAnalysis](https://developer.apple.com/documentation/sensitivecontentanalysis/scsensitivityanalysis) · [SCSensitivityAnalysisPolicy](https://developer.apple.com/documentation/sensitivecontentanalysis/scsensitivityanalysispolicy) · [entitlement](https://developer.apple.com/documentation/BundleResources/Entitlements/com.apple.developer.sensitivecontentanalysis.client) · [About Sensitive Content Warning](https://support.apple.com/en-us/105071)
- [objc2-sensitive-content-analysis (crates.io)](https://crates.io/crates/objc2-sensitive-content-analysis) · [pyobjc-framework-SensitiveContentAnalysis (PyPI)](https://pypi.org/project/pyobjc-framework-SensitiveContentAnalysis/) · [pyobjc issue #654](https://github.com/ronaldoussoren/pyobjc/issues/654)
