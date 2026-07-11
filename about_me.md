# Arnav Sagar — Profile

## Contact
- **Email:** arnavsagar1510@gmail.com
- **Phone:** +91-6284962948
- **GitHub:** github.com/zibranxo
- **LinkedIn:** linkedin.com/in/arnvsr
- **LeetCode:** arnvsr

---

## Education

**Delhi Technological University (DTU)**, New Delhi
B.Tech – Software Engineering | CGPA: **8.75/10** | Jul 2024 – Jun 2028

**Kendriya Vidyalaya Sector-47**, Chandigarh
12th: 93% | 10th: 94.4% | 2020–2023

---

## Work Experience

### Regavis Labs Pvt Ltd — Machine Learning Intern
*Jun 2026 – Present*

Architecting a two-stage cascade for real-time audio deepfake detection on RingWave, a live-calling platform: a Voice Activity Detector gates audio into a lightweight LFCC-LCNN first-pass screener, escalating only ambiguous segments to an SSL-based second-stage verifier (XLSR-53 + AASIST). This cuts average inference compute cost by **85%** versus running full verification on every frame. Includes a Hindi/Indic data bootstrap strategy to cover underrepresented accents, VoIP-channel augmentation for real-world call degradation, and a two-policy aggregation layer that reconciles the two stages' outputs into a single call-level verdict.

### 5G Lab, Department of Telecommunications (GoI) — DTU — Research Intern
*Jun 2025*
*Stack: YOLOv8, OpenCV, URLLC, MEC, CUDA*

- Engineered a **sub-25ms P95** on-device inference pipeline via per-channel entropy calibration — **4× model compression**, **1.8× throughput**, <1.5% mAP loss — deployed across CUDA, Metal MPS, and CPU backends.
- Built a real-time multi-person tracker (Lucas-Kanade optical flow + IoU-based keypoint association) feeding a self-supervised trajectory autoencoder (~1,600 params, <0.1ms/track) over a simulated 5G URLLC transport layer (2ms latency, 0.1% packet loss).
- **Presented at PEC Chandigarh** as original research.

### AIMS-DTU — Research Intern (LLM Safety)
*Jun 2025 – Jul 2025*

Deployed a session-aware jailbreak and toxicity classifier: MiniLM cosine similarity, SHAP token attribution for explainability, adversarial input normalization (homoglyph, zero-width character, base64 decoding), Redis caching, and ONNX-optimized inference. Served via production FastAPI.

---

## Projects

### CLASP — Claude API Switching Proxy
*GitHub: github.com/zibranxo/clasp*

Rate-limit-aware multi-provider proxy routing LLM traffic (Claude Code / Codex-style clients) across **18 inference providers**, using a pre-emptive token-bucket limiter and multi-key pool rotation with circuit breakers to eliminate 429 errors. Includes an async priority queue with SSE keep-alive absorption to buffer requests during quota exhaustion, a three-tier LRU/SQLite/semantic (FAISS) cache, and an Anthropic↔OpenAI protocol translator. **934 passing tests.**

### CAF-OTSRNet — Cross-Attention Fusion Thermal Super-Resolution
*Stack: PyTorch, Streamlit, Gradio, Rasterio, GeoPandas*
*GitHub: github.com/zibranxo/caf-otsr-net · Demo available*

Reconstructs high-resolution thermal infrared imagery from low-resolution thermal input, guided by high-resolution multispectral optical imagery, on a dataset provided by **ISRO**. Two-stage geometric alignment (Spatial Transformer Network for global affine correction + a learned deformable displacement field for sub-pixel local correction) feeds three modality-specific encoders (thermal, optical + NDVI/NDWI/NDBI spectral indices, Sobel-based texture). A cross-attention fusion block queries optical detail through thermal features — preventing optical structures from overriding true temperature signal — gated by a per-pixel texture-safety network that suppresses hallucinated edges. A progressive Laplacian decoder reconstructs the output in stages (e.g. 32×32 → 64×64 → 128×128) with residual corrections and thermal-encoder skip connections, and the model additionally outputs pixel-wise uncertainty estimates. **PSNR +15.74%, SSIM +8.22%** vs. SOTA.

### Retrieval Augmentation System (RAGS)
*Stack: Python, FAISS, BM25, cross-encoder, ChromaDB, bge-m3, bge-reranker-v2-m3*

Multi-stage RAG pipeline processing 15k+ PDF pages: semantic chunking, hierarchical small-to-big indexing, HyDE-based query expansion, and sub-question decomposition, improving answer relevance by **38%** (GPT-4-judge evaluated). Hybrid dense-sparse retrieval (FAISS + BM25) with RRF fusion, cross-encoder reranking, MMR diversity selection, contextual compression, and a DistilBERT-based CRAG hallucination evaluator — reducing hallucination rate by **47%**. Built with a full ablation-study framework to isolate each component's contribution; plain Python orchestration, no LangChain.

### Hybrid CTC-Attention Scene Text Recognition (OCR)
*Stack: Python, PyTorch, VGG-CNN, BiLSTM, Bahdanau attention, W&B*
*GitHub: github.com/zibranxo/ocr-ctc*

CRNN architecture: VGG-style CNN backbone + 2-layer BiLSTM encoder + Bahdanau attention decoder, trained with a joint CTC and cross-entropy loss and beam search decoding to stabilize early training and refine character alignment. **89% word accuracy** on IIIT5K after training on 9M MJSynth images.

### Text Splitting and Embedding Visualizer
*Stack: JavaScript, HTML/CSS*
*GitHub: github.com/zibranxo/chunk · Live demo: text-split.netlify.app*

Interactive visualization tool for RAG chunking strategies — character, token, sentence, recursive splitting — with a cosine semantic similarity matrix, chunk-size statistics, and overlap metrics. Already deployed and publicly accessible; useful for explaining retrieval concepts to non-technical audiences live.

### AI vs Human Text Classification System
*Stack: Python, PyTorch, RoBERTa (fine-tuned), scikit-learn, GPU XGBoost*

Benchmarked 8+ classical and ensemble models (Logistic Regression, SVM, Naive Bayes, Decision Trees, Random Forest + AdaBoost, kNN, ANN, attention-based LSTMs) against a fine-tuned RoBERTa on ~200K samples. **RoBERTa accuracy: 0.9996.** GPU-accelerated training pipeline cut training time from 35 min to 6 min.

---

## Technical Skills

- **Languages:** Python, C/C++, JavaScript, HTML/CSS, SQL
- **ML/AI Frameworks:** PyTorch, TensorFlow, ONNX, HuggingFace Transformers, OpenCV, Pandas, NumPy, LlamaIndex
- **Infrastructure & Tools:** FastAPI, Docker, Git, W&B, ROS2 (Humble), Streamlit, Redis, FAISS, AsyncIO, WebSockets

---

## Achievements

- **National Finalist, Smart India Hackathon 2025** — Ahmedabad, India (ISRO problem statement)
- **Coordinator**, Business Bulls, DTU (Finance & Strategy Club)

---

## What I'm Looking For

**Role type:** AI/ML Engineering internship or early-career role

**Strongest fit areas:**
- Applied AI / LLM systems — RAG pipelines, multi-provider LLM infrastructure, prompt engineering
- LLM safety, red-teaming, and evaluation
- Edge AI / on-device ML inference
- Audio/speech ML — deepfake and spoof detection, real-time inference under compute constraints

**Location:** Delhi / Bangalore / remote (India) or remote global

**Why relevant at 2nd year:** Three research/ML internships (AIMS-DTU, 5G Lab, Regavis Labs) completed before the end of second year, each producing a shipped or production-integrated system — not coursework. I build end-to-end: data pipelines → model training/architecture design → production serving (FastAPI) → live demos. I can explain design decisions under pressure, debug in real time, and translate technical work into communication a non-technical team can act on.