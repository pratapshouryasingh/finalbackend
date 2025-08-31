// -------------------- Imports --------------------
import express from "express";
import dotenv from "dotenv";
import multer from "multer";
import cors from "cors";
import path from "path";
import fs from "fs";
import fsp from "fs/promises";
import { spawn } from "child_process";
import mongoose from "mongoose";
import { fileURLToPath } from "url";
import History from "./historyModel.js";

// -------------------- Env --------------------
dotenv.config();
const app = express();
const PORT = process.env.PORT || 5000;
const PYTHON = process.env.PYTHON_BIN || "python3"; // e.g. /usr/bin/python3 or /home/ubuntu/venv/bin/python

// Resolve project root robustly (independent of where PM2/systemd starts the app)
const __filename = fileURLToPath(import.meta.url);
const PROJECT_ROOT = path.dirname(__filename);

// -------------------- MongoDB --------------------
const MONGODB_URI = process.env.MONGODB_URI;
mongoose
  .connect(MONGODB_URI, {
    serverSelectionTimeoutMS: 20000,
    socketTimeoutMS: 120000,
    // keep alive to avoid idle disconnects
    keepAlive: true,
    keepAliveInitialDelay: 300000,
  })
  .then(() => console.log("🟢 Connected to MongoDB Atlas"))
  .catch((err) => console.error("❌ MongoDB connection error:", err));

// -------------------- CORS --------------------
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
    origin: (origin, cb) => {
      if (!origin) return cb(null, true); // allow Postman/curl
      if (allowedOrigins.includes(origin)) return cb(null, true);
      return cb(new Error(`❌ Not allowed by CORS: ${origin}`));
    },
    methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allowedHeaders: ["Origin", "Content-Type", "Accept", "Authorization"],
    credentials: true,
  })
);

// Preflight
app.options("*", cors());

// Extra safety (mirrors via proxy too)
app.use((req, res, next) => {
  res.header("Access-Control-Allow-Origin", req.headers.origin || "*");
  res.header("Access-Control-Allow-Credentials", "true");
  res.header(
    "Access-Control-Allow-Headers",
    "Origin, X-Requested-With, Content-Type, Accept, Authorization"
  );
  res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
  next();
});

// -------------------- Middleware --------------------
app.use(express.json({ limit: "50mb" })); // align with upload size
app.set("trust proxy", true);

// -------------------- Health Routes --------------------
app.get("/health", (_req, res) => res.status(200).json({ status: "ok" }));
app.get("/", (_req, res) => res.status(200).send("✅ Server is running"));

// -------------------- Multer --------------------
const TMP_UPLOADS = path.join(PROJECT_ROOT, "tmp_uploads");
fs.mkdirSync(TMP_UPLOADS, { recursive: true });

const upload = multer({
  dest: TMP_UPLOADS,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50 MB
  fileFilter: (_req, file, cb) =>
    file.mimetype === "application/pdf"
      ? cb(null, true)
      : cb(new Error("Only PDF files allowed")),
});

// -------------------- Helpers --------------------
function makeJobDirs(toolName) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const jobId = `job_${ts}`;
  const toolsRoot = path.join(PROJECT_ROOT, "tools", toolName); // <<< stable path
  const inputDir = path.join(toolsRoot, "input", jobId);
  const outputDir = path.join(toolsRoot, "output", jobId);
  fs.mkdirSync(inputDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  return { jobId, inputDir, outputDir, toolsRoot };
}

function runPython({ inputDir, outputDir, toolsRoot }) {
  return new Promise((resolve) => {
    const mainPy = path.join(toolsRoot, "main.py");

    const child = spawn(PYTHON, [mainPy, "--input", inputDir, "--output", outputDir], {
      cwd: toolsRoot,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1", // flush prints
      },
    });

    let stdout = "", stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));

    child.on("error", (err) => {
      console.error("❌ spawn error:", err);
      resolve({ stdout: "", stderr: String(err), warn: true });
    });

    child.on("close", (code) => {
      if (code === 0) resolve({ stdout });
      else {
        console.warn(`⚠ Python exited with code ${code}: ${stderr}`);
        resolve({ stdout, warn: true, stderr });
      }
    });
  });
}

async function waitForOutputs(dir, timeoutMs = 180000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const files = await fsp.readdir(dir);
    if (files.length > 0) return files;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("No output files generated within timeout");
}

// -------------------- Generic Processor --------------------
async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0)
      return res.status(400).json({ error: "No files uploaded" });

    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(toolName);
    inputDir = idir;

    // Optional override config.json
    if (settings) {
      try {
        const parsed = typeof settings === "string" ? JSON.parse(settings) : settings;
        await fsp.writeFile(
          path.join(toolsRoot, "config.json"),
          JSON.stringify(parsed, null, 2)
        );
        console.log(`✅ ${toolName} config.json updated`);
      } catch (e) {
        console.error("❌ config.json parse error:", e);
      }
    }

    // Move uploaded files
    await Promise.all(
      req.files.map(async (f, i) => {
        const safe = f.originalname?.replace(/[\\/]/g, "_") || `file_${i}.pdf`;
        await fsp.rename(f.path, path.join(inputDir, safe));
      })
    );

    // Run Python
    const py = await runPython({ inputDir, outputDir, toolsRoot });
    if (py.warn) console.warn(`[${toolName}] python warn:\n`, py.stderr || py.stdout);

    // Collect outputs
    const files = await waitForOutputs(outputDir);
    const outputs = files
      .filter((f) => f.endsWith(".pdf") || f.endsWith(".xlsx"))
      .map((name) => ({
        name,
        url: `/api/${toolName.toLowerCase()}/download/${jobId}/${name}`,
      }));

    // Save history
    let history = [];
    if (userId) {
      await new History({ userId, toolName, jobId, outputs }).save();
      history = await History.find({ userId }).sort({ timestamp: -1 }).limit(10);
    }

    res.json({ success: true, tool: toolName, jobId, outputs, history });
  } catch (err) {
    console.error(`${toolName} error:`, err);
    res.status(500).json({ error: String(err.message || err) });
  } finally {
    if (inputDir && fs.existsSync(inputDir)) {
      await fsp.rm(inputDir, { recursive: true, force: true });
    }
  }
}

// -------------------- Processing Routes --------------------
app.post("/api/flipkart", upload.array("files", 50), (req, res) =>
  processTool("FlipkartCropper", req, res)
);
app.post("/api/meesho", upload.array("files", 50), (req, res) =>
  processTool("MeeshoCropper", req, res) // <-- fixed spelling
);
app.post("/api/jiomart", upload.array("files", 50), (req, res) =>
  processTool("JioMartCropper", req, res)
);

// -------------------- Download Route --------------------
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  const { tool, jobId, filename } = req.params;
  const filePath = path.join(PROJECT_ROOT, "tools", tool, "output", jobId, filename);
  if (fs.existsSync(filePath)) return res.download(filePath);
  res.status(404).json({ error: "File not found" });
});

// -------------------- User History --------------------
app.get("/api/history/:userId", async (req, res) => {
  try {
    const { userId } = req.params;
    const history = await History.find({ userId }).sort({ timestamp: -1 });
    res.json({ success: true, history });
  } catch (err) {
    console.error("❌ History fetch error:", err);
    res.status(500).json({ error: "Failed to fetch user history" });
  }
});

// -------------------- Admin Routes --------------------
app.get("/api/admin/files", async (_req, res) => {
  try {
    const toolsRoot = path.join(PROJECT_ROOT, "tools");
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

app.delete("/api/admin/files/:tool/:jobId/:filename", async (req, res) => {
  try {
    const { tool, jobId, filename } = req.params;
    const filePath = path.join(PROJECT_ROOT, "tools", tool, "output", jobId, filename);

    if (!fs.existsSync(filePath)) return res.status(404).json({ error: "File not found" });

    await fsp.unlink(filePath);
    res.json({ success: true, message: "File deleted" });
  } catch (err) {
    console.error("❌ Delete error:", err);
    res.status(500).json({ error: "Failed to delete file" });
  }
});

// -------------------- Stability: process guards --------------------
process.on("unhandledRejection", (reason) => {
  console.error("🛑 Unhandled Rejection:", reason);
});
process.on("uncaughtException", (err) => {
  console.error("🛑 Uncaught Exception:", err);
});
const shutdown = async () => {
  console.log("👋 Shutting down...");
  try { await mongoose.connection.close(); } catch {}
  process.exit(0);
};
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

// -------------------- Start Server --------------------
app.listen(PORT, () => console.log(`✅ Server running on port ${PORT}`));
