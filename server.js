import express from "express";
import dotenv from "dotenv";
import multer from "multer";
import cors from "cors";
import path from "path";
import fs from "fs";
import fsp from "fs/promises";
import { spawn } from "child_process";
import mongoose from "mongoose";
import History from "./historyModel.js";

dotenv.config();
const app = express();
const PORT = process.env.PORT || 5000;

// -------------------- CORS --------------------
const allowedOrigins = [
  "https://shippinglablecropper-git-main-pratapshouryasinghs-projects.vercel.app",
  "http://localhost:5173",
  "http://localhost:5000",
];

app.use(
  cors({
    origin: function (origin, callback) {
      if (!origin || allowedOrigins.includes(origin)) {
        callback(null, true);
      } else {
        callback(new Error("❌ Not allowed by CORS"));
      }
    },
    credentials: true,
  })
);

// Extra safeguard: set headers for ALL responses
app.use((req, res, next) => {
  res.header("Access-Control-Allow-Origin", req.headers.origin || "*");
  res.header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS");
  res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
  res.header("Access-Control-Allow-Credentials", "true");
  if (req.method === "OPTIONS") return res.sendStatus(200);
  next();
});

app.use(express.json({ limit: "50mb" }));

// -------------------- MongoDB --------------------
const MONGODB_URI = process.env.MONGODB_URI;
mongoose
  .connect(MONGODB_URI)
  .then(() => console.log("🟢 Connected to MongoDB Atlas"))
  .catch((err) => console.error("❌ MongoDB connection error:", err));

// -------------------- Health --------------------
app.get("/health", (_req, res) => res.json({ status: "ok" }));
app.get("/", (_req, res) => res.send("✅ Server is running"));

// -------------------- Multer --------------------
const TMP_UPLOADS = path.join(process.cwd(), "tmp_uploads");
fs.mkdirSync(TMP_UPLOADS, { recursive: true });

const upload = multer({
  dest: TMP_UPLOADS,
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    if (file.mimetype === "application/pdf") cb(null, true);
    else cb(new Error("Only PDF files allowed"));
  },
});

// -------------------- Helpers --------------------
const toolMap = {
  flipkart: "FlipkartCropper",
  meesho: "MeshooCropper",
  jiomart: "JioMartCropper",
};

function makeJobDirs(toolName) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const jobId = `job_${ts}`;
  const toolsRoot = path.join(process.cwd(), "tools", toolName);
  const inputDir = path.join(toolsRoot, "input", jobId);
  const outputDir = path.join(toolsRoot, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir, toolsRoot };
}

function runPython({ inputDir, outputDir, toolsRoot }) {
  return new Promise((resolve) => {
    const mainPy = path.join(toolsRoot, "main.py");
    const child = spawn("python3", [mainPy, "--input", inputDir, "--output", outputDir], {
      cwd: toolsRoot,
    });

    let stdout = "",
      stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));

    child.on("close", (code) => {
      if (code === 0) resolve({ stdout });
      else {
        console.warn(`⚠ Python exited with code ${code}: ${stderr}`);
        resolve({ stdout, warn: true });
      }
    });
  });
}

async function waitForOutputs(dir, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const files = await fsp.readdir(dir);
    if (files.length > 0) return files;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("No output files generated in time");
}

async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(toolName);
    inputDir = idir;

    if (settings) {
      try {
        const parsed = JSON.parse(settings);
        await fsp.writeFile(path.join(toolsRoot, "config.json"), JSON.stringify(parsed, null, 2));
        console.log(`✅ ${toolName} config.json overridden`);
      } catch (e) {
        console.error("❌ Failed to save config.json:", e);
      }
    }

    await Promise.all(
      req.files.map(async (f, idx) => {
        const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safeName));
      })
    );

    await runPython({ inputDir, outputDir, toolsRoot });

    const files = await waitForOutputs(outputDir);

    const outputs = files
      .filter((f) => f.endsWith(".pdf") || f.endsWith(".xlsx"))
      .map((name) => ({
        name,
        url: `/api/${toolName.toLowerCase()}/download/${jobId}/${name}`,
      }));

    let updatedHistory = [];
    if (userId) {
      const historyEntry = new History({ userId, toolName, jobId, outputs });
      await historyEntry.save();

      updatedHistory = await History.find({ userId })
        .sort({ timestamp: -1 })
        .limit(10);
    }

    res.json({ success: true, tool: toolName, jobId, outputs, history: updatedHistory });
  } catch (err) {
    console.error(`${toolName} error:`, err);
    res.status(500).json({ error: err.message });
  } finally {
    if (inputDir && fs.existsSync(inputDir)) {
      await fsp.rm(inputDir, { recursive: true, force: true });
      console.log(`🗑 Deleted input folder: ${inputDir}`);
    }
  }
}

// -------------------- Routes --------------------
app.post("/api/:tool", upload.array("files", 50), (req, res) => {
  const key = req.params.tool.toLowerCase();
  const toolName = toolMap[key];
  if (!toolName) return res.status(400).json({ error: "Unknown tool" });
  processTool(toolName, req, res);
});

// Download
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  const { tool, jobId, filename } = req.params;
  const key = tool.toLowerCase();
  const toolName = toolMap[key];
  if (!toolName) return res.status(400).json({ error: "Unknown tool" });

  const filePath = path.join(process.cwd(), "tools", toolName, "output", jobId, filename);
  if (fs.existsSync(filePath)) res.download(filePath);
  else res.status(404).json({ error: "File not found" });
});

// User history
app.get("/api/history/:userId", async (req, res) => {
  try {
    const { userId } = req.params;
    const userHistory = await History.find({ userId })
      .sort({ timestamp: -1 })
      .limit(10);
    res.json({ success: true, history: userHistory });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch history" });
  }
});

// -------------------- Start --------------------
app.listen(PORT, () => console.log(`🚀 Server running at http://localhost:${PORT}`));
