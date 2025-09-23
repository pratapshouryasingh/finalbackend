// backend/server.js
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

// ===== MongoDB Connection =====
const MONGODB_URI = process.env.MONGODB_URI;
mongoose
  .connect(MONGODB_URI)
  .then(() => console.log("🟢 Connected to MongoDB Atlas"))
  .catch((err) => console.error("❌ MongoDB connection error:", err));

// ===== CORS Setup =====
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

// ===== Health Routes =====
app.get("/health", (_req, res) => res.status(200).json({ status: "ok" }));
app.get("/", (_req, res) => res.send("🚀 Server is running"));

// ===== Multer (Uploads) =====
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

// ===== Tool Map =====
const TOOL_MAP = {
  flipkart: "FlipkartCropper",
  meshoo: "MeshooCropper", // Keeping intentional typo
  jiomart: "JioMartCropper",
  cropper: "FrontendCropper",
};

// ===== Helpers =====
function validateTool(toolName) {
  const folder = TOOL_MAP[toolName.toLowerCase()] || toolName;
  const toolPath = path.join(process.cwd(), "tools", folder);

  if (!fs.existsSync(toolPath)) throw new Error(`Tool not found: ${folder}`);
  if (!fs.existsSync(path.join(toolPath, "main.py")))
    throw new Error(`main.py not found in ${folder}`);

  return folder;
}

function makeJobDirs(toolName) {
  const ts = Date.now();
  const jobId = `job_${ts}`;
  const root = path.join(process.cwd(), "tools", toolName);
  const inputDir = path.join(root, "input", jobId);
  const outputDir = path.join(root, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir, toolsRoot: root };
}

function runPython({ inputDir, outputDir, toolsRoot }) {
  return new Promise((resolve, reject) => {
    const mainPy = path.join(toolsRoot, "main.py");
    const configPath = path.join(toolsRoot, "config.json");
    const args = ["--input", inputDir, "--output", outputDir];
    if (fs.existsSync(configPath)) args.push("--config", configPath);

    const pythonCmd = process.platform === "win32" ? "python" : "python3";
    console.log(`🐍 Running: ${pythonCmd} ${mainPy} ${args.join(" ")}`);

    const child = spawn(pythonCmd, [mainPy, ...args], { cwd: toolsRoot });

    let stderr = "";
    child.stdout.on("data", (d) => console.log(`🐍 stdout: ${d.toString().trim()}`));
    child.stderr.on("data", (d) => {
      stderr += d.toString();
      console.error(`🐍 stderr: ${d.toString().trim()}`);
    });

    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Python failed with code ${code}: ${stderr}`));
    });
    child.on("error", reject);
  });
}

async function waitForOutputs(dir, timeoutMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const files = await fsp.readdir(dir);
      const valid = files.filter((f) => f.endsWith(".pdf") || f.endsWith(".xlsx"));
      if (valid.length > 0) return valid;
    } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("No outputs within timeout");
}

// ===== Core Processor =====
async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    const validated = validateTool(toolName);
    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(validated);
    inputDir = idir;

    if (settings) {
      try {
        const parsed = typeof settings === "string" ? JSON.parse(settings) : settings;
        const configPath = path.join(toolsRoot, "config.json");
        await fsp.writeFile(configPath, JSON.stringify(parsed, null, 2));
      } catch (e) {
        console.error("❌ Failed to override config.json", e);
      }
    }

    await Promise.all(
      req.files.map(async (f, idx) => {
        const safe = f.originalname?.replace(/[^a-zA-Z0-9._-]/g, "_") || `file_${idx}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safe));
      })
    );

    await runPython({ inputDir, outputDir, toolsRoot });
    const files = await waitForOutputs(outputDir);

    const apiTool =
      Object.keys(TOOL_MAP).find((k) => TOOL_MAP[k] === validated) ||
      validated.toLowerCase();

    const outputs = files.map((name) => ({
      name,
      url: `/api/${apiTool}/download/${jobId}/${encodeURIComponent(name)}`,
    }));

    let updatedHistory = [];
    if (userId) {
      try {
        const entry = new History({
          userId,
          toolName: validated,
          jobId,
          outputs,
          fileCount: req.files.length,
        });
        await entry.save();
        updatedHistory = await History.find({ userId })
          .sort({ timestamp: -1 })
          .limit(10);
      } catch (e) {
        console.error("❌ History save failed:", e);
      }
    }

    res.json({
      success: true,
      tool: validated,
      jobId,
      outputs,
      history: updatedHistory,
      message: `Processed ${req.files.length} files`,
    });
  } catch (err) {
    res.status(500).json({ error: err.message || String(err) });
  } finally {
    if (inputDir) await fsp.rm(inputDir, { recursive: true, force: true });
    if (req.files) {
      for (const f of req.files) if (fs.existsSync(f.path)) await fsp.unlink(f.path);
    }
  }
}

// ===== Routes =====
app.post("/api/flipkart", upload.array("files", 50), (req, res) =>
  processTool("flipkart", req, res)
);
app.post("/api/meesho", upload.array("files", 50), (req, res) =>
  processTool("meshoo", req, res) // Using intentional typo
);
app.post("/api/jiomart", upload.array("files", 50), (req, res) =>
  processTool("jiomart", req, res)
);
app.post("/api/cropper", upload.array("files", 50), (req, res) =>
  processTool("cropper", req, res)
);
app.post("/api/tool/:toolName", upload.array("files", 50), (req, res) =>
  processTool(req.params.toolName, req, res)
);

// ===== Download Route =====
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  try {
    const { tool, jobId, filename } = req.params;
    const decodedFilename = decodeURIComponent(filename);
    
    // Security: Prevent path traversal
    if (decodedFilename.includes("..") || decodedFilename.includes("/") || decodedFilename.includes("\\")) {
      return res.status(400).json({ error: "Invalid filename" });
    }

    const toolFolder = validateTool(tool);
    const filePath = path.join(process.cwd(), "tools", toolFolder, "output", jobId, decodedFilename);
    
    if (!fs.existsSync(filePath)) return res.status(404).json({ error: "File not found" });

    if (decodedFilename.endsWith(".pdf")) res.setHeader("Content-Type", "application/pdf");
    if (decodedFilename.endsWith(".xlsx"))
      res.setHeader(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      );
    res.setHeader("Content-Disposition", `attachment; filename="${decodedFilename}"`);
    res.download(filePath);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// ===== Tools List =====
app.get("/api/tools", (req, res) => {
  try {
    const root = path.join(process.cwd(), "tools");
    const tools = fs
      .readdirSync(root)
      .filter((t) => fs.statSync(path.join(root, t)).isDirectory());
    res.json({ success: true, tools });
  } catch {
    res.status(500).json({ error: "Failed to list tools" });
  }
});

// ===== User History =====
app.get("/api/history/:userId", async (req, res) => {
  try {
    const history = await History.find({ userId: req.params.userId })
      .sort({ timestamp: -1 })
      .limit(10);
    res.json({ success: true, history });
  } catch {
    res.status(500).json({ error: "Failed to fetch history" });
  }
});

// ===== Admin File List =====
app.get("/api/admin/files", async (req, res) => {
  try {
    const root = path.join(process.cwd(), "tools");
    let allFiles = [];
    
    if (!fs.existsSync(root)) {
      return res.json({ success: true, files: [] });
    }

    for (const tool of await fsp.readdir(root)) {
      const toolPath = path.join(root, tool);
      if (!(await fsp.stat(toolPath)).isDirectory()) continue;

      const outputRoot = path.join(toolPath, "output");
      if (!fs.existsSync(outputRoot)) continue;

      for (const jobId of await fsp.readdir(outputRoot)) {
        const jobDir = path.join(outputRoot, jobId);
        const stat = await fsp.stat(jobDir).catch(() => null);
        if (!stat || !stat.isDirectory()) continue;

        for (const name of await fsp.readdir(jobDir)) {
          if (!name.endsWith(".pdf") && !name.endsWith(".xlsx")) continue;
          const stats = await fsp.stat(path.join(jobDir, name));
          const apiTool =
            Object.keys(TOOL_MAP).find((k) => TOOL_MAP[k] === tool) || tool.toLowerCase();
          allFiles.push({
            tool,
            jobId,
            name,
            size: stats.size,
            modified: stats.mtime,
            url: `/api/${apiTool}/download/${jobId}/${encodeURIComponent(name)}`,
          });
        }
      }
    }
    allFiles.sort((a, b) => b.modified - a.modified);
    res.json({ success: true, files: allFiles });
  } catch (err) {
    console.error("Admin files error:", err);
    res.status(500).json({ error: "Failed to list admin files" });
  }
});

// ===== Admin Delete =====
app.delete("/api/admin/files/:tool/:jobId/:filename", async (req, res) => {
  try {
    const { tool, jobId, filename } = req.params;
    const decodedFilename = decodeURIComponent(filename);
    
    // Security: Prevent path traversal
    if (decodedFilename.includes("..") || decodedFilename.includes("/") || decodedFilename.includes("\\")) {
      return res.status(400).json({ error: "Invalid filename" });
    }

    const folder = TOOL_MAP[tool.toLowerCase()] || tool;
    const filePath = path.join(process.cwd(), "tools", folder, "output", jobId, decodedFilename);

    if (!fs.existsSync(filePath)) return res.status(404).json({ error: "File not found" });
    await fsp.unlink(filePath);
    res.json({ success: true, message: "File deleted" });
  } catch (err) {
    console.error("Delete error:", err);
    res.status(500).json({ error: "Failed to delete file" });
  }
});

// ===== Error Handler =====
app.use((err, req, res, _next) => {
  console.error("❌ Uncaught error:", err);
  res.status(500).json({ error: "Internal server error", details: err.message });
});

// ===== Start =====
app.listen(PORT, () => console.log(`✅ Server running at http://localhost:${PORT}`));