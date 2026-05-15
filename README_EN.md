<div align="center">

<h1 style="font-size: 2.5em; font-weight: bold;"><img src="https://lib.cnvtn.com/zda/android_logo.png" alt="ZDA Logo" width="48" align="center" style="vertical-align: middle;" /> ZDA (知搭)</h1>

<p style="font-size: 1.2em; font-weight: 500; margin-top: 10px;">
  <b>Rendering fleeting thoughts into tangible knowledge.</b>
</p>

Stop searching for fragmented information, stop enduring dry and lengthy AI-generated texts, and stop being trapped by boring PPT templates.<br>
ZDA focuses on only one thing: **Letting knowledge naturally grow its own visuals, rhythm, and subtitles, evolving into a dynamic flow that can be "watched".**

<p style="margin-top: 15px;">
  <img alt="AI Knowledge View" src="https://img.shields.io/badge/AI-Knowledge%20VIEW-7C3AED?style=for-the-badge">
  <img alt="Animated HTML" src="https://img.shields.io/badge/OUTPUT-ANIMATED%20HTML-2F53EA?style=for-the-badge">
  <img alt="WebM Video" src="https://img.shields.io/badge/EXPORT-WEBM%20VIDEO-0EA5E9?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/LICENSE-CC%20BY--NC--ND%204.0-ef9421?style=for-the-badge&logo=creativecommons&logoColor=white">
</p>

<p>
  <a href="./README_EN.md"><b>English</b></a> | <a href="./README.md">简体中文</a>
</p>

<p>
  <a href="#-official-website-experience-the-magic-with-one-click">Live Demo</a> ·
  <a href="#-redefining-knowledge-acquisition">Features</a> ·
  <a href="#-a-dimensional-strike-against-information-overload">Pain Points Solved</a> ·
  <a href="#️-lightning-fast-local-engine">Local Deployment</a> ·
  <a href="#-core-architecture--api-overview">Core Architecture</a>
</p>

</div>

---

## 🌐 Official Website: Experience the Magic with One Click

No need to study the architecture or configure the environment. Just open the official website, throw a question at it, and watch how it grows from a simple query into a vivid "micro-knowledge exhibition hall".

<div align="center">

<a href="https://zda.cnvtn.com">
  <img src="https://lib.cnvtn.com/zda/index.png" alt="ZDA Official Website Preview" width="920" style="border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" />
</a>

> **[👉 Click to enter the ZDA official website and start your Knowledge View](https://zda.cnvtn.com)**

</div>

---

## ✨ Redefining Knowledge Acquisition

**ZDA, your AI Knowledge Architect.**

Try throwing a brainstorming or hardcore question at it:
* *Why can't even light escape a black hole?*
* *What exactly is the Byzantine Generals Problem struggling with?*
* *Why does procrastination make you feel more tired?*
* *Why is the A* algorithm so fast at finding paths?*

It won't just throw you a seemingly professional but sleep-inducing lengthy article. It transforms into a **storyboard director**, precisely breaking down your question: what suspense to introduce first, when to pause, which sentence needs highlighted subtitles, and which scene should cut to animations.

What is ultimately delivered is an **immersive, playable dynamic view**—not only with smooth HTML animations, precise timelines, and voiceovers, but it also generates an exclusive sharing link, or even supports one-click recording into a direct video output.

---

## 🎯 A Dimensional Strike Against "Information Overload"

In this era where obtaining information is easy, "truly understanding" remains an anti-human challenge. Search engines give links, large models pile up text, video platforms cram fragments—the conclusions are there, but the **path to understanding is broken**.

We execute a dimensional strike against boring "text piling", turning it into a smooth "View Flow":

| Traditional Dilemma 😩 | Real Feeling | ZDA Solution 💡 |
| :--- | :--- | :--- |
| **Bookmarking means gathering dust** | Long articles are hard to chew, bookmarking = learning, screenshotting = reviewing. | **Knowledge Slicing**: Cooperating with dynamic storyboards and short subtitles, "feeding" knowledge right to you. |
| **AI feels like a manual** | Screen full of dry text, brain feels no waves after reading. | **Concept Visualization**: Letting text leap into a dynamic playback flow with sound and pictures. |
| **Making pop-science is exhausting** | Finding materials, writing scripts, editing & voiceovers—hairline warning. | **Industrial-grade Direct Output**: Automatically handles scripts, timelines, voiceovers, and visual construction. |
| **Hardcore knowledge is tough** | Want to learn but can't find an entry point, want to give up after reading two lines. | **Cognitive Slide**: Starting from the question, paving a smooth path from shallow to deep. |
| **Black-box generation** | Dumbly waiting for the progress bar, completely unaware of where it's stuck. | **Fully Transparent Progress**: Review, generation, audio, assembly—every step's progress is visible in real-time. |

---

## 🚀 Output Format: Dynamic Knowledge View

Key takeaway: ZDA focuses on **Knowledge View**. This is a brand new content format: easier than reading articles, simpler than making videos, smarter than PPTs, and better to share than pure text AI.

It is naturally suited for the following scenarios: **explaining new concepts from scratch**, **breaking down hardcore scientific mechanisms**, **inventorying historical causal chains**, and **step-by-step diagramming of technical principles**.

| Module Example | Direct Experience | Format Description |
| :--- | :--- | :--- |
| **Landscape View Template** | [Preview Template](https://zda.cnvtn.com/templates/landscape.html) | The underlying core HTML-driven template for Knowledge Views. |
| **Standard View Playback** | [Watch Demo](https://zda.cnvtn.com/view/knowledge-demo) | The standard knowledge playback page after rendering, with interactive controls. |
| **Immersive Fullscreen** | [Fullscreen Demo](https://zda.cnvtn.com/view/knowledge-demo/fullscreen) | Stripping away extra UI to bring an immersive viewing experience close to a short film. |
| **Recording & Exporting** | [Record Demo](https://zda.cnvtn.com/record/knowledge-demo) | One-click rendering and recording of the Knowledge View into a WebM video format. |
| **Open API** | [API Docs](https://zda.cnvtn.com/docs) | Developer Zone: APIs for generation tasks, playback, payment, authentication, etc., are all open. |

---

## 🛠️ Lightning-Fast Local Engine

Want to run this generation pipeline locally? You only need to prepare MySQL, model/TTS configurations, and runtime keys. No hidden pitfalls, start instantly.

### 1. Initialize Database
The project relies on MySQL 8.0. Please ensure local or remote service is available:
```text
Default connection: 127.0.0.1:3306/zda

```

*Note: Database SQL is not published with the source code. Ensure the database table structure is consistent with the mapping in `app/db/models.py`.*

### 2. Inject Core Configurations

The main project configuration is located at `app/config.yml`. For security, sensitive keys are dynamically read from the database:

```yaml
# Core keys that need to be configured in the database
dynamic_view_model_profile.api_key: "Your model invocation key"
website_content.content_key: "Your runtime key collection" # Including payments, email, login signatures, etc.

```

### 3. Build Runtime Environment

One-click installation of all required core dependencies:

```bash
pip install fastapi uvicorn sqlalchemy pymysql pydantic pyyaml httpx python-multipart langchain-core langchain-openai openai google-genai dashscope paho-mqtt

```

### 4. Ignite and Start

Start the service directly via the Python module or Uvicorn:

```bash
# Method 1: Python module startup
python -m app.main

# Method 2: Uvicorn ASGI startup
uvicorn app.main:app --host 0.0.0.0 --port 5000

```

### 5. Heartbeat Check

After the service starts, verify the health status:

```bash
curl [http://127.0.0.1:5000/health](http://127.0.0.1:5000/health)

```

**Expected Response:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "status": "ok"
  }
}

```

---

## 🔌 Core Architecture & API Overview

The system has clear microservice routing splits based on business modules:

| Route Prefix | Module Responsibility | Core Capability Description |
| --- | --- | --- |
| `/health` | **Probe** | Node health check and survival confirmation. |
| `/api/dynamic-view` | **Engine Core** | Create view generation tasks, poll progress, play HTML, read audio/comment streams. |
| `/api/website` | **Frontend Aggregation** | Homepage exhibition hall config, theme batch scheduling, model option distribution, generation session management. |
| `/api/auth` | **Security Auth** | Identity authentication, email captcha issuance, and verification. |
| `/api/payments` | **Asset Hub** | ZPAY order creation, payment callback notification, membership benefits distribution, compute Credit settlement. |
| `/api/chat` | **Real-time Comm** | MQTT channel initialization, long-connection session list management, barrage and message push. |
| `/api/admin` | **Admin Control** | System-level resource scheduling and global data inventory. |

---

## ⏱️ Automated Scheduling (CRON)

ZDA doesn't just respond to frontend requests; the backend comes with a robust scheduled maintenance mechanism (using MySQL `GET_LOCK` to ensure distributed singleton execution in a multi-Worker environment):

* **Zombie Task Recovery**: Upon service startup, automatically marks historical unfinished hanging tasks as failed, refusing frontend "infinite spinning".
* **Asynchronous Dispatch**: Based on the configured cycle, periodically consumes and dispatches view generation tasks from the queue.
* **Quota Reset**: Automatically clears and recovers expired Credits daily at `00:00 (UTC+8)`.
* **Content Refresh**: Refreshes the official website theme batch daily at `00:00 (UTC+8)`; recalculates grand view recommendation data every 6 hours to maintain frontend content vitality.

---

## 📄 Open Source License

This project is open-sourced under the **[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)** protocol.
This project is source-available for learning, research, and non-commercial review only. Commercial use and distribution of modified versions are not permitted without written permission from the author.

* **👤 Attribution**: You must retain the "ZDA" attribution mark.
* **🚫 Non-Commercial**: It is strictly forbidden to use this source code, products, and derivative services for any commercial profit purposes.
* **🔒 No Derivatives**: Distributing modified, repackaged, or closed-source versions is not allowed.

---

## 📮 Join Us

In this era of information overload, join us in reshaping the format of knowledge.

* **📧 Business & Cooperation**: `your-email@example.com`
* **🐛 Bugs & Suggestions**: Welcome to submit feedback in [GitHub Issues](https://www.google.com/search?q=%23)
* **💬 Developer Community**: [Click to scan the QR code to join the communication group](https://www.google.com/search?q=%23)

```

```
