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

  // baseDir is inside the tool folder and specific to the user
  const baseDir = path.join(process.cwd(), "tools", "MeshooCropper");
  const inputDir = path.join(baseDir, "input", jobId);
  const outputDir = path.join(baseDir, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir, baseDir };
}

// Run Python tool
function runPython({ baseDir /* user specific tool dir */, inputDir, outputDir }) {
  return new Promise((resolve, reject) => {
    const mainPy = path.join(baseDir, "..", "main.py"); // if main.py is in tools/meshooCropper (adjust if different)
    // if main.py is under the user dir, you can use path.join(baseDir, "main.py")
    // Use platform-appropriate python command
    const pythonCmd = process.platform === "win32" ? "python" : "python3";

    // Pass args as absolute paths in case main.py is updated later to accept them
    const args = [mainPy, "--input", inputDir, "--output", outputDir];

    console.log("🐍 Spawning python:", pythonCmd, args.join(" "));
    // Set cwd to the user-specific baseDir so "input" and "output" dirs are visible if main.py uses relative paths
    const child = spawn(pythonCmd, args, { cwd: baseDir, env: process.env });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (d) => {
      const s = d.toString();
      stdout += s;
      console.log("py stdout:", s.trim());
    });
    child.stderr.on("data", (d) => {
      const s = d.toString();
      stderr += s;
      console.error("py stderr:", s.trim());
    });

    child.on("close", (code) => {
      console.log(`python process closed with code ${code}`);
      if (code === 0) resolve({ stdout, stderr, code });
      else {
        // resolve anyway so the waitForOutputs can inspect output folder
        resolve({ stdout, stderr, code, warn: true });
      }
    });

    child.on("error", (err) => {
      console.error("Failed to start python process:", err);
      reject(err);
    });
  });
}

// Wait for output PDFs (increased timeout) - keep your long timeout but log more
async function waitForOutputs(dir, timeoutMs = 20 * 60 * 1000) { // 20 minutes default
  const start = Date.now();
  console.log(`⏳ Waiting for outputs in: ${dir}`);
  while (Date.now() - start < timeoutMs) {
    try {
      const files = await fsp.readdir(dir);
      if (files.length > 0) {
        console.log("Found files:", files);
        return files;
      }
    } catch (e) {
      // Directory may not exist yet
      // console.warn("waitForOutputs read error:", e.message);
    }
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

    const { jobId, inputDir, outputDir, baseDir } = makeUserJobDirs(userId);

    // Write config.json in the user-specific tool root (baseDir)
    const configPath = path.join(baseDir, "config.json");
    await fsp.writeFile(configPath, JSON.stringify(parsedSettings, null, 2));
    console.log("WROTE config to:", configPath);

    // Move uploaded PDFs
    await Promise.all(req.files.map(async (f, idx) => {
      const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
      const dest = path.join(inputDir, safeName);
      await fsp.rename(f.path, dest);
      console.log(`Moved uploaded file -> ${dest}`);
    }));

    // Run Python tool with cwd = baseDir so main.py sees baseDir/input & baseDir/output
    const pyResult = await runPython({ baseDir, inputDir, outputDir });
    console.log("Python finished:", { code: pyResult.code });

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

