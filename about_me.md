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
12th: 93% | 10th: 94.4% | 2022–2023

---

## Work Experience

### AIMS-DTU — Research Intern (LLM Safety)
*Jun 2025 – Jul 2025*

- Architected a 3-stage LLM moderation pipeline: sub-10ms regex-based filters → semantic embeddings → fine-tuned DistilBERT augmented with XGBoost + LOF on TF-IDF features to surface zero-day prompt injections outside the classifier's training distribution.
- Engineered a risk-scored mitigation engine across 5 harm categories with 4-tier decision logic using contextual NER + POS proximity analysis.
- Validated against red-teaming prompts on OpenChat-3.5 with GPT-4o-mini cross-validation.
- Served the entire pipeline via a production FastAPI server.

### 5G LAB, Department of Telecommunications, DTU — Summer Research Intern
*Jun 2025*  
*Stack: YOLOv8, OpenCV, URLLC, MEC, CUDA*

- Engineered a sub-25ms P95 end-to-end on-device inference pipeline via per-channel entropy calibration: 4× model compression, 1.8× throughput at <1.5% mAP loss.
- Deployed per-person Lucas-Kanade optical flow on alternate frames with IoU-based keypoint association across CUDA, Metal MPS, and CPU backends.
- Designed a self-supervised trajectory autoencoder (~1,600 params, <0.1ms/track) augmenting 5 geometric IDS rules to surface 6 behavioural threat types without labeled data.
- Simulated 5G URLLC transport layer (1–5ms latency, 0.1% packet loss) for structured edge-to-core threat delivery.
- **Presented research at PEC Chandigarh** as original work.

---

## Projects

### YTRAG — YouTube RAG Chatbot
*Stack: JavaScript, OpenAI Embeddings, IndexedDB, Vite*  
*GitHub: github.com/zibranxo/ytrag*

Full end-to-end RAG pipeline over YouTube transcripts: transcript fetch → semantic chunking (~1000 tokens, 100-char overlap) → embed with text-embedding-3-small (1536 dims) → store in IndexedDB → cosine similarity retrieval (TOP_K=3, threshold=0.5) → LLM generation. Dual provider: OpenAI or local Ollama. Timestamp-linked citations, chat history, deployed UI. Key insight: RAG quality lives in retrieval design, not generation — threshold and chunk overlap tuning outweighs model choice.

---

### LLM Safety Shield (Personal Project)
*Stack: Python, MiniLM, DistilBERT, SHAP, Redis, ONNX, FastAPI*

Session-aware jailbreak and toxicity classifier with MiniLM cosine similarity, SHAP token attribution for explainability, adversarial normalization (homoglyph, zero-width char, base64 decoding), Redis caching for sub-ms repeat queries, ONNX-optimized inference, and active-learning flywheel for continuous improvement.

---

### Vera Bot — magicpin AI Challenge
*Stack: FastAPI, Python, model-agnostic (OpenAI / Anthropic / Gemini / DeepSeek / Groq)*

Prompt-dispatch composer for merchant ↔ customer messaging. Trigger-kind routing, 4 context inputs, post-LLM validator (URL strip, CTA check, empty-body guard), auto-reply detector (regex + repeat counter: 1 flag → warn, 2 → 24h hold, 3 → exit), intent transition handler. Temperature=0 for deterministic outputs.

---

### Retrieval Augmentation System
*Stack: Python, FAISS, BM25, cross-encoder, LlamaIndex*

Multi-stage RAG over PDF corpora with semantic chunking, hierarchical small-to-big indexing, HyDE-based query expansion, and sub-question decomposition. Hybrid dense-sparse retrieval (FAISS + BM25) with RRF fusion, cross-encoder reranking, MMR diversity selection, contextual compression, and CRAG-based hallucination suppression.

---

### JAILS — Jailbreak Instruction Leakage Detection System
*Stack: Python, scikit-learn (Gradient Boosting, RF, LR), LOF, TF-IDF*

Hybrid jailbreak/prompt-injection detector. Features: semantic similarity, linguistic heuristics, pattern matching (instruction overrides, role-play, coercion, privilege escalation), LOF for zero-day attacks. Fully interpretable output: SAFE/JAILBREAK + confidence score + risk level + feature-level reasoning.

---

### CAF-OTSRNet — Optical-Guided Thermal Super-Resolution
*Stack: PyTorch, Streamlit, Gradio, Rasterio, GeoPandas*

Triple-encoder cross-attention fusion for thermal SR. Two-stage alignment: global affine (Spatial Transformer Network) + learned deformable local correction. Texture-guided safety mechanism suppresses hallucinated thermal edges via per-pixel gating. Progressive Laplacian decoder. Physics-aware losses. Pixel-wise uncertainty estimation. **PSNR +15.74%, SSIM +8.22% vs SOTA on ISRO dataset.**

---

### Text Splitting & Embedding Visualizer
*Stack: JavaScript, HTML/CSS*  
**Live demo: https://text-split.netlify.app/**

Interactive visualization of RAG chunking strategies (character, token, sentence, recursive) with metrics, overlap highlighting, and chunk-size stats. Cosine semantic similarity matrix + k-Means clustering with PCA/t-SNE/UMAP projections. Already deployed and publicly accessible.

---

### AI vs Human Text Classification
*Stack: Python, PyTorch, RoBERTa (fine-tuned), scikit-learn, GPU XGBoost*

14+ model benchmark on ~200K samples. TF-IDF + stylometric features. GPU pipeline: 35 min → 6 min (5.8× speedup). **RoBERTa accuracy: 0.9996.**

---

### Hybrid CTC-Attention Scene Text Recognition (OCR)
*Stack: Python, PyTorch, VGG-CNN, BiLSTM, Bahdanau attention, W&B*

CRNN: VGG backbone + 2-layer BiLSTM + CTC head + attention decoder. Joint loss: 0.3·CTC + 0.7·Attention. ~89% word accuracy on IIIT5K, trained on 9M MJSynth images.

---

### ZeroFall+ — Multi-Agent Security System
*Stack: Python, RoBERTa, LLaMA, LoRA, PyTorch, Flask, Docker, blockchain*

Unified WAF + EDR pipeline with 6 autonomous agents. RoBERTa for anomaly detection, blockchain behavioral hashing for O(1) immutable threat memory, LoRA fine-tuning without catastrophic forgetting.

---

## Technical Skills

- **Languages:** Python, C/C++, JavaScript, HTML/CSS, SQL
- **ML/AI Frameworks:** PyTorch, TensorFlow, ONNX, HuggingFace Transformers, OpenCV, Pandas, NumPy, LlamaIndex
- **Infrastructure & Tools:** FastAPI, Docker, Git, W&B, ROS2 (Humble), Streamlit, Redis, FAISS

---

## Achievements

- **National Finalist, Smart India Hackathon 2025** — Ahmedabad (ISRO problem statement)
- **Research Presentation, PEC Chandigarh** — AI-Based Intrusion Detection in 5G Networks
- **Coordinator**, Business Bulls, DTU (Finance & Strategy Club)

---

## What I'm Looking For

**Role type:** AI/ML Engineering internship or early-career role

**Strongest fit areas:**
- Applied AI / LLM systems — RAG pipelines, agent workflows, prompt engineering
- LLM safety, red-teaming, and evaluation
- Edge AI / on-device ML inference
- Agentic AI and voice AI (currently building RingWave: real-time voice deepfake detection using AASIST/WavLM/wav2vec2-XLS-R)

**Location:** Delhi / Bangalore / remote (India) or remote global

**Why relevant at 2nd year:** Two research internships completed before end of first year, both producing shipped systems — not coursework. I build end-to-end: data pipelines → model training → FastAPI serving → live demos. I can explain design decisions under pressure, debug in real time, and translate technical work into communication a non-technical team can act on.

All subjects should be: Internship Application- Arnav Sagar(DTU)- AI-ML
