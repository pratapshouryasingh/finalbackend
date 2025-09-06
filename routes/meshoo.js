// backend/routes/meshoo.js
import express from "express";
import multer from "multer";
import fs from "fs";
import fsp from "fs/promises";
import path from "path";
import { spawn } from "child_process";

const router = express.Router();

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

// Create job-specific input/output folders
function makeUserJobDirs(userId) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const jobId = `job_${ts}`;
  const baseDir = path.join(process.cwd(), "tools", "meshooCropper", userId);
  const inputDir = path.join(baseDir, "input", jobId);
  const outputDir = path.join(baseDir, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir, baseDir };
}

// Run Python tool
function runPython({ inputDir, outputDir, configPath, toolsRoot }) {
  return new Promise((resolve, reject) => {
    const mainPy = path.join(toolsRoot, "main.py");
    const args = ["--input", inputDir, "--output", outputDir, "--config", configPath];

    const child = spawn("python", [mainPy, ...args], { cwd: toolsRoot });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));

    child.on("close", (code) => {
      if (code === 0) {
        console.log(`✅ Python finished: ${stdout}`);
        resolve({ stdout });
      } else {
        console.warn(`⚠ Python exited with code ${code}. stderr: ${stderr}`);
        // resolve anyway to check output folder
        resolve({ stdout, warn: true });
      }
    });
  });
}

// Wait for output PDFs (increased timeout)
async function waitForOutputs(dir, timeoutMs = 1200000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const files = await fsp.readdir(dir);
    if (files.length > 0) return files;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("No output files generated within timeout");
}

// Upload endpoint
router.post("/upload", upload.array("files", 50), async (req, res) => {
  try {
    const { userId, settings } = req.body;
    if (!userId) return res.status(400).json({ error: "Missing userId" });
    if (!req.files?.length) return res.status(400).json({ error: "No files uploaded" });

    let parsedSettings = {};
    if (settings) {
      try { parsedSettings = JSON.parse(settings); } 
      catch { console.warn("⚠ Invalid settings JSON"); }
    }

    const { jobId, inputDir, outputDir } = makeUserJobDirs(userId);
    const toolsRoot = path.join(process.cwd(), "tools", "meshooCropper");

    // Write config.json in tool root
    const configPath = path.join(toolsRoot, "config.json");
    await fsp.writeFile(configPath, JSON.stringify(parsedSettings, null, 2));

    // Move uploaded PDFs
    await Promise.all(req.files.map(async (f, idx) => {
      const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
      await fsp.rename(f.path, path.join(inputDir, safeName));
    }));

    // Run Python tool
    await runPython({ inputDir, outputDir, configPath, toolsRoot });

    // Wait for output PDFs
    const files = await waitForOutputs(outputDir);

    const outputs = files.map((name) => ({
      name,
      url: `/api/meshoo/download/${userId}/${jobId}/${name}`,
    }));

    res.json({ success: true, userId, jobId, outputs });
  } catch (err) {
    console.error("Meshoo error:", err);
    res.status(500).json({ error: err.message });
  }
});

// Download endpoint
router.get("/download/:userId/:jobId/:filename", async (req, res) => {
  const { userId, jobId, filename } = req.params;
  const filePath = path.join(
    process.cwd(),
    "tools",
    "meshooCropper",
    userId,
    "output",
    jobId,
    filename
  );

  if (fs.existsSync(filePath)) res.download(filePath);
  else res.status(404).json({ error: "File not found" });
});

export default router;
