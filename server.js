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
  "https://shippinglablecropper.vercel.app"
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

// -------------------- JSON & URL Encoded Limits --------------------
app.use(express.json({ limit: "50mb" }));
app.use(express.urlencoded({ extended: true, limit: "50mb" }));

// -------------------- MongoDB --------------------
const MONGODB_URI = process.env.MONGODB_URI;
mongoose
  .connect(MONGODB_URI)
  .then(() => console.log("🟢 Connected to MongoDB Atlas"))
  .catch((err) => console.error("❌ MongoDB connection error:", err));

// -------------------- Health Check --------------------
app.get("/health", (_req, res) => res.json({ status: "ok" }));
app.get("/", (_req, res) => res.send("✅ Server is running"));

// -------------------- Multer Upload --------------------
const TMP_UPLOADS = path.join(process.cwd(), "tmp_uploads");
fs.mkdirSync(TMP_UPLOADS, { recursive: true });

const upload = multer({
  dest: TMP_UPLOADS,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50 MB
  fileFilter: (_req, file, cb) => {
    if (file.mimetype === "application/pdf") cb(null, true);
    else cb(new Error("Only PDF files allowed"));
  },
});

// -------------------- Helpers --------------------
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

    let stdout = "", stderr = "";
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

// -------------------- Core Processor --------------------
async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(toolName);
    inputDir = idir;

    // Save config.json if provided
  // Save config.json if provided
if (settings) {
  try {
    const parsed = JSON.parse(settings);

    // Ensure toolName maps correctly
    const toolMap = {
      flipkart: "FlipkartCropper",
      meesho: "MeshooCropper",
      jiomart: "JioMartCropper",
    };
    const properTool = toolMap[toolName.toLowerCase()] || toolName;

    const configPath = path.join(process.cwd(), "tools", properTool, "config.json");
    await fsp.writeFile(configPath, JSON.stringify(parsed, null, 2));

    console.log(`✅ ${properTool} config.json overridden at ${configPath}`);
  } catch (e) {
    console.error("❌ Failed to save config.json:", e);
  }
}


    // Move uploaded files to input dir
    await Promise.all(
      req.files.map(async (f, idx) => {
        const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safeName));
      })
    );

    // Run Python script
    await runPython({ inputDir, outputDir, toolsRoot });

    // Wait for output
    const files = await waitForOutputs(outputDir);

    const outputs = files
      .filter((f) => f.endsWith(".pdf") || f.endsWith(".xlsx"))
      .map((name) => ({
        name,
        url: `/api/${toolName}/download/${jobId}/${name}`,
      }));

    // Save history
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
    // Cleanup input
    if (inputDir && fs.existsSync(inputDir)) {
      await fsp.rm(inputDir, { recursive: true, force: true });
      console.log(`🗑 Deleted input folder: ${inputDir}`);
    }
  }
}

// -------------------- Routes --------------------
app.post("/api/flipkart", upload.array("files", 50), (req, res) =>
  processTool("FlipkartCropper", req, res)
);
app.post("/api/meesho", upload.array("files", 50), (req, res) =>
  processTool("MeshooCropper", req, res)
);
app.post("/api/jiomart", upload.array("files", 50), (req, res) =>
  processTool("JioMartCropper", req, res)
);

// Download output
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  const { tool, jobId, filename } = req.params;

  // Map lowercase tool → correct folder
  const toolMap = {
    flipkart: "FlipkartCropper",
    meesho: "MeshooCropper",
    jiomart: "JioMartCropper",
  };

  const toolName = toolMap[tool.toLowerCase()] || tool;
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

// Admin list files
app.get("/api/admin/files", async (_req, res) => {
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
    res.status(500).json({ error: "Failed to list admin files" });
  }
});

// Admin delete file
app.delete("/api/admin/files/:tool/:jobId/:filename", async (req, res) => {
  try {
    const { tool, jobId, filename } = req.params;
    const filePath = path.join(process.cwd(), "tools", tool, "output", jobId, filename);

    if (!fs.existsSync(filePath)) return res.status(404).json({ error: "File not found" });

    await fsp.unlink(filePath);
    res.json({ success: true, message: "File deleted" });
  } catch (err) {
    res.status(500).json({ error: "Failed to delete file" });
  }
});

// -------------------- Start --------------------
app.listen(PORT, () => console.log(`🚀 Server running at http://localhost:${PORT}`));
