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

// 🔗 Connect to MongoDB
const MONGODB_URI = process.env.MONGODB_URI;
mongoose
  .connect(MONGODB_URI)
  .then(() => console.log("🟢 Connected to MongoDB Atlas"))
  .catch((err) => console.error("❌ MongoDB connection error:", err));

// --- CORS setup ---
const allowedOrigins = [
  "https://shippinglablecropper.vercel.app",
  "https://shippinglablecropper-git-main-pratapshouryasinghs-projects.vercel.app",
  "http://localhost:5173",
  "http://localhost:5000",
  "https://www.shippinglabelcrop.in",
  "https://shippinglabelcrop.in",
  "https://aws.shippinglabelcrop.in",
];

app.use(
  cors({
    origin: (origin, callback) => {
      if (!origin || allowedOrigins.includes(origin)) {
        callback(null, true);
      } else {
        callback(new Error("Not allowed by CORS"));
      }
    },
    credentials: true,
    methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization"],
  })
);

app.use(express.json());

// --- Health check ---
app.get("/health", (_req, res) => {
  res.status(200).json({ status: "ok" });
});
app.get("/", (_req, res) => {
  res.status(200).send("Server is running ✅");
});

// Temporary upload folder
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

// Create per-job folders
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

// Run Python tool
function runPython({ inputDir, outputDir, toolsRoot }) {
  return new Promise((resolve) => {
    const mainPy = path.join(toolsRoot, "main.py");
    const configPath = path.join(outputDir, "config.json");

    const args = ["--input", inputDir, "--output", outputDir];
    if (fs.existsSync(configPath)) {
      args.push("--config", configPath);
    }

    const child = spawn("python3", [mainPy, ...args], { cwd: toolsRoot });

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

// Wait for output files (PDF + Excel)
async function waitForOutputs(dir, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const files = await fsp.readdir(dir);
    if (files.length > 0) return files;
    await new Promise((r) => setTimeout(r, 500));
  }
  const errorPath = path.join(dir, "error_pages.pdf");
  if (fs.existsSync(errorPath)) return ["error_pages.pdf"];
  throw new Error("No output files generated within timeout");
}

// Generic processor
async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(toolName);
    inputDir = idir;

    // Job-specific config
    if (settings) {
      try {
        const parsedSettings = JSON.parse(settings);
        const configPath = path.join(outputDir, "config.json");
        await fsp.writeFile(configPath, JSON.stringify(parsedSettings, null, 2));
        console.log(`✅ ${toolName} job-specific config.json created:`, parsedSettings);
      } catch (e) {
        console.error(`❌ Failed to create job config.json:`, e);
      }
    }

    // Move uploaded files into job input
    await Promise.all(
      req.files.map(async (f, idx) => {
        const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safeName));
      })
    );

    // Run Python
    await runPython({ inputDir, outputDir, toolsRoot });

    // Wait for outputs
    const files = await waitForOutputs(outputDir);

    // Return both PDF and Excel
    const outputs = files
      .filter((f) => f.endsWith(".pdf") || f.endsWith(".xlsx"))
      .map((name) => ({
        name,
        url: `/api/${toolName.toLowerCase()}/download/${jobId}/${name}`,
      }));

    // Save history
    let updatedHistory = [];
    if (userId) {
      const historyEntry = new History({ userId, toolName, jobId, outputs });
      await historyEntry.save();
      updatedHistory = await History.find({ userId }).sort({ timestamp: -1 }).limit(10);
    }

    res.json({ success: true, tool: toolName, jobId, outputs, history: updatedHistory });
  } catch (err) {
    console.error(`${toolName} error:`, err);
    res.status(500).json({ error: String(err.message || err) });
  } finally {
    if (inputDir && fs.existsSync(inputDir)) {
      try {
        await fsp.rm(inputDir, { recursive: true, force: true });
        console.log(`🗑 Deleted input folder: ${inputDir}`);
      } catch (e) {
        console.error(`❌ Failed to delete input folder: ${inputDir}`, e);
      }
    }
  }
}

// Routes
app.post("/api/flipkart", upload.array("files", 50), (req, res) =>
  processTool("FlipkartCropper", req, res)
);
app.post("/api/meesho", upload.array("files", 50), (req, res) =>
  processTool("MeshooCropper", req, res)
);
app.post("/api/jiomart", upload.array("files", 50), (req, res) =>
  processTool("JioMartCropper", req, res)
);

// Download route
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  const { tool, jobId, filename } = req.params;
  const filePath = path.join(process.cwd(), "tools", tool, "output", jobId, filename);
  if (fs.existsSync(filePath)) res.download(filePath);
  else res.status(404).json({ error: "File not found" });
});

// User history
app.get("/api/history/:userId", async (req, res) => {
  try {
    const { userId } = req.params;
    const userHistory = await History.find({ userId }).sort({ timestamp: -1 }).limit(10);
    res.json({ success: true, history: userHistory });
  } catch (err) {
    console.error("❌ Failed to fetch user history:", err);
    res.status(500).json({ error: "Failed to fetch user history" });
  }
});

// Admin - list all files
app.get("/api/admin/files", async (req, res) => {
  try {
    const toolsRoot = path.join(process.cwd(), "tools");
    const tools = await fsp.readdir(toolsRoot);

    let allFiles = [];
    for (const tool of tools) {
      const outputRoot = path.join(toolsRoot, tool, "output");
      if (!fs.existsSync(outputRoot)) continue;

      const jobs = await fsp.readdir(outputRoot);
      for (const jobId of jobs) {
        const jobDir = path.join(outputRoot, jobId);
        if (!fs.lstatSync(jobDir).isDirectory()) continue;

        const files = (await fsp.readdir(jobDir)).filter(
          (f) => f.endsWith(".pdf") || f.endsWith(".xlsx")
        );

        files.forEach((name) => {
          const filePath = path.join(jobDir, name);
          const stats = fs.existsSync(filePath) ? fs.statSync(filePath) : { size: 0 };

          allFiles.push({
            tool,
            jobId,
            name,
            size: stats.size,
            url: `/api/${tool}/download/${jobId}/${name}`,
          });
        });
      }
    }

    res.json({ success: true, files: allFiles });
  } catch (err) {
    console.error("❌ Admin file list error:", err);
    res.status(500).json({ error: "Failed to list admin files" });
  }
});

// Admin - delete file
app.delete("/api/admin/files/:tool/:jobId/:filename", async (req, res) => {
  try {
    const { tool, jobId, filename } = req.params;
    const filePath = path.join(process.cwd(), "tools", tool, "output", jobId, filename);

    if (!fs.existsSync(filePath)) {
      return res.status(404).json({ error: "File not found" });
    }

    await fsp.unlink(filePath);
    console.log(`🗑 Deleted file: ${filePath}`);
    res.json({ success: true, message: "File deleted" });
  } catch (err) {
    console.error("❌ Error deleting file:", err);
    res.status(500).json({ error: "Failed to delete file" });
  }
});

app.listen(PORT, () => console.log(`✅ Server running at http://localhost:${PORT}`));
