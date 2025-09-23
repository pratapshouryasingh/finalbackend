// backend/routes/cropper.js
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

// Create job-specific input/output folders per user
function makeUserJobDirs(userId) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const jobId = `job_${ts}`;
  const baseDir = path.join(process.cwd(), "tools", "FrontendCropper", userId);
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
    const args = ["--input", inputDir, "--output", outputDir];
    if (configPath) args.push("--config", configPath);

    const pythonCmd = process.platform === "win32" ? "python" : "python3";
    const child = spawn(pythonCmd, [mainPy, ...args], { cwd: toolsRoot });

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
        resolve({ stdout, warn: true });
      }
    });

    child.on("error", (err) => {
      reject(err);
    });
  });
}

// Wait for output PDFs
async function waitForOutputs(dir, timeoutMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const files = await fsp.readdir(dir);
    if (files.length > 0) return files;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("No output files generated within timeout");
}

// Upload endpoint
// Upload endpoint - return download URLs instead of file blob
router.post("/upload", upload.array("files", 50), async (req, res) => {
  try {
    // ... existing code ...

    // Wait for output PDFs
    const files = await waitForOutputs(outputDir);

    const outputs = files.map((name) => ({
      name,
      url: `/api/cropper/download/${userId}/${jobId}/${name}`,
      downloadUrl: `${req.protocol}://${req.get('host')}/api/cropper/download/${userId}/${jobId}/${name}`
    }));

    // Return the download info instead of the file
    res.json({ 
      success: true, 
      userId, 
      jobId, 
      outputs,
      downloadUrl: outputs[0]?.downloadUrl // First file download URL
    });
    
  } catch (err) {
    console.error("Cropper error:", err);
    res.status(500).json({ error: err.message });
  }
});

// Download endpoint
router.get("/download/:userId/:jobId/:filename", async (req, res) => {
  const { userId, jobId, filename } = req.params;
  const filePath = path.join(
    process.cwd(),
    "tools",
    "FrontendCropper",
    userId,
    "output",
    jobId,
    filename
  );

  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ error: "File not found" });
  }

  // ✅ Set proper headers and send the PDF file
  res.download(filePath, filename, (err) => {
    if (err) {
      console.error("Download error:", err);
      res.status(500).json({ error: "Failed to download file" });
    }
  });
});

export default router;
