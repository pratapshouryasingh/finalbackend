// backend/routes/jiomart.js
import express from "express";
import multer from "multer";
import fs from "fs";
import fsp from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TMP_UPLOADS = path.join(__dirname, "..", "tmp_uploads");
fs.mkdirSync(TMP_UPLOADS, { recursive: true });

// Multer config
const upload = multer({
  dest: TMP_UPLOADS,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50MB
  fileFilter: (_req, file, cb) => {
    if (file.mimetype === "application/pdf") cb(null, true);
    else cb(new Error("Only PDF files allowed"));
  },
});

const router = express.Router();

/**
 * Create user-specific job folders
 */
function makeUserJobDirs(userId) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const jobId = `job_${ts}`;
  const baseDir = path.join(__dirname, "..", "tools", "JioMartCropper", userId);
  const inputDir = path.join(baseDir, "input", jobId);
  const outputDir = path.join(baseDir, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir };
}

/**
 * Run Python tool
 */
function runPython({ inputDir, outputDir, configPath }) {
  return new Promise((resolve, reject) => {
    const toolsRoot = path.join(__dirname, "..", "tools", "JioMartCropper");
    const mainPy = path.join(toolsRoot, "main.py");

    const child = spawn(
      "python",
      ["-u", mainPy, "--input", inputDir, "--output", outputDir, "--config", configPath],
      { cwd: toolsRoot }
    );

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));

    child.on("close", (code) => {
      if (code === 0) resolve({ stdout });
      else reject(new Error(stderr || `Python exited with code ${code}`));
    });
  });
}

// Upload endpoint
router.post("/upload", upload.array("files", 50), async (req, res) => {
  try {
    const { userId, settings } = req.body;

    if (!userId) return res.status(400).json({ error: "Missing userId in request" });
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    let parsedSettings = {};
    try {
      if (settings) parsedSettings = JSON.parse(settings);
    } catch (err) {
      console.warn("Invalid settings JSON:", err.message);
    }

    const { jobId, inputDir, outputDir } = makeUserJobDirs(userId);

    // Write config.json for this job
    const configPath = path.join(inputDir, "config.json");
    await fsp.writeFile(configPath, JSON.stringify(parsedSettings, null, 2));

    // Move uploaded PDFs
    await Promise.all(
      req.files.map(async (f, idx) => {
        const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safeName));
      })
    );

    // Run Python tool
    await runPython({ inputDir, outputDir, configPath });

    // List outputs
    const outputs = (await fsp.readdir(outputDir)).map((name) => ({
      name,
      url: `/api/jiomart/download/${userId}/${jobId}/${name}`,
    }));

    res.json({ success: true, userId, jobId, outputs });
  } catch (err) {
    console.error("JioMart error:", err);
    res.status(500).json({ error: err.message });
  }
});

// Download endpoint
router.get("/download/:userId/:jobId/:filename", async (req, res) => {
  const { userId, jobId, filename } = req.params;
  const filePath = path.join(
    __dirname,
    "..",
    "tools",
    "JioMartCropper",
    userId,
    "output",
    jobId,
    filename
  );

  if (fs.existsSync(filePath)) res.download(filePath);
  else res.status(404).json({ error: "File not found" });
});

export default router;
