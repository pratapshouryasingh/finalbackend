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

// --- Tool name map ---
const TOOL_MAP = {
  flipkart: "FlipkartCropper",
  meesho: "MeshooCropper",
  jiomart: "JioMartCropper",
};

// Validate tool exists
function validateTool(toolName) {
  const toolFolder = TOOL_MAP[toolName.toLowerCase()] || toolName;
  const toolPath = path.join(process.cwd(), "tools", toolFolder);
  
  if (!fs.existsSync(toolPath)) {
    throw new Error(`Tool not found: ${toolName}`);
  }
  
  const mainPyPath = path.join(toolPath, "main.py");
  if (!fs.existsSync(mainPyPath)) {
    throw new Error(`main.py not found for tool: ${toolName}`);
  }
  
  return toolFolder;
}

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
  return new Promise((resolve, reject) => {
    const mainPy = path.join(toolsRoot, "main.py");
    const configPath = path.join(outputDir, "config.json");

    const args = ["--input", inputDir, "--output", outputDir];
    if (fs.existsSync(configPath)) {
      args.push("--config", configPath);
    }

    console.log(`🐍 Running Python: python3 ${mainPy} ${args.join(" ")}`);

    const child = spawn("python3", [mainPy, ...args], { cwd: toolsRoot });

    let stdout = "",
      stderr = "";
    child.stdout.on("data", (d) => {
      stdout += d.toString();
      console.log(`🐍 Python stdout: ${d.toString().trim()}`);
    });
    
    child.stderr.on("data", (d) => {
      stderr += d.toString();
      console.error(`🐍 Python stderr: ${d.toString().trim()}`);
    });

    child.on("close", (code) => {
      if (code === 0) {
        console.log(`✅ Python process completed successfully`);
        resolve({ stdout });
      } else {
        console.error(`❌ Python exited with code ${code}: ${stderr}`);
        reject(new Error(`Python process failed with code ${code}: ${stderr}`));
      }
    });

    child.on("error", (err) => {
      console.error("❌ Failed to start Python process:", err);
      reject(err);
    });
  });
}

// Wait for output files (PDF + Excel)
async function waitForOutputs(dir, timeoutMs = 120000) {
  const start = Date.now();
  console.log(`⏳ Waiting for outputs in: ${dir}`);
  
  while (Date.now() - start < timeoutMs) {
    try {
      const files = await fsp.readdir(dir);
      if (files.length > 0) {
        const validFiles = files.filter(f => f.endsWith(".pdf") || f.endsWith(".xlsx"));
        if (validFiles.length > 0) {
          console.log(`✅ Found output files: ${validFiles.join(", ")}`);
          return validFiles;
        }
      }
      await new Promise((r) => setTimeout(r, 1000));
    } catch (err) {
      console.error("Error reading output directory:", err);
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  
  // Check for error file as fallback
  const errorPath = path.join(dir, "error_pages.pdf");
  if (fs.existsSync(errorPath)) {
    console.log("⚠️ Found error file as output");
    return ["error_pages.pdf"];
  }
  
  throw new Error("No output files generated within timeout");
}

// Generic processor
async function processTool(toolName, req, res) {
  let inputDir;
  try {
    const { userId, settings } = req.body;
    if (!req.files || req.files.length === 0) {
      return res.status(400).json({ error: "No files uploaded" });
    }

    // Validate tool exists
    const validatedToolName = validateTool(toolName);
    const { jobId, inputDir: idir, outputDir, toolsRoot } = makeJobDirs(validatedToolName);
    inputDir = idir;

    console.log(`🛠️ Processing ${toolName} job: ${jobId} with ${req.files.length} files`);

    // Job-specific config
    if (settings) {
      try {
        const parsedSettings = typeof settings === 'string' ? JSON.parse(settings) : settings;
        const configPath = path.join(outputDir, "config.json");
        await fsp.writeFile(configPath, JSON.stringify(parsedSettings, null, 2));
        console.log(`✅ ${toolName} job-specific config.json created`);
      } catch (e) {
        console.error(`❌ Failed to create job config.json:`, e);
      }
    }

    // Move uploaded files into job input
    await Promise.all(
      req.files.map(async (f, idx) => {
        const safeName = f.originalname?.replace(/[\\/]/g, "_") || `file_${idx}.pdf`;
        const destPath = path.join(inputDir, safeName);
        await fsp.rename(f.path, destPath);
        console.log(`📄 Moved file to: ${destPath}`);
      })
    );

    // Run Python
    await runPython({ inputDir, outputDir, toolsRoot });

    // Wait for outputs
    const files = await waitForOutputs(outputDir);

    // Return both PDF and Excel
    const outputs = files.map((name) => {
      const apiTool = Object.keys(TOOL_MAP).find((k) => TOOL_MAP[k] === validatedToolName) || validatedToolName.toLowerCase();
      return {
        name,
        url: `/api/${apiTool}/download/${jobId}/${name}`,
      };
    });

    // Save history
    let updatedHistory = [];
    if (userId) {
      try {
        const historyEntry = new History({ 
          userId, 
          toolName: validatedToolName, 
          jobId, 
          outputs,
          fileCount: req.files.length
        });
        await historyEntry.save();
        updatedHistory = await History.find({ userId }).sort({ timestamp: -1 }).limit(10);
        console.log(`💾 Saved history for user: ${userId}`);
      } catch (historyError) {
        console.error("❌ Failed to save history:", historyError);
      }
    }

    res.json({ 
      success: true, 
      tool: validatedToolName, 
      jobId, 
      outputs, 
      history: updatedHistory,
      message: `Processed ${req.files.length} files successfully`
    });
    
    console.log(`✅ Completed processing job: ${jobId}`);
    
  } catch (err) {
    console.error(`${toolName} processing error:`, err);
    res.status(500).json({ 
      error: String(err.message || err),
      details: "Check server logs for more information"
    });
  } finally {
    // Clean up input directory
    if (inputDir && fs.existsSync(inputDir)) {
      try {
        await fsp.rm(inputDir, { recursive: true, force: true });
        console.log(`🗑 Deleted input folder: ${inputDir}`);
      } catch (e) {
        console.error(`❌ Failed to delete input folder: ${inputDir}`, e);
      }
    }
    
    // Clean up temporary upload files
    if (req.files) {
      for (const file of req.files) {
        if (file.path && fs.existsSync(file.path)) {
          try {
            await fsp.unlink(file.path);
          } catch (e) {
            console.error(`❌ Failed to delete temp file: ${file.path}`, e);
          }
        }
      }
    }
  }
}

// Routes for each tool
app.post("/api/flipkart", upload.array("files", 50), (req, res) =>
  processTool("FlipkartCropper", req, res)
);
app.post("/api/meesho", upload.array("files", 50), (req, res) =>
  processTool("MeshooCropper", req, res)
);
app.post("/api/jiomart", upload.array("files", 50), (req, res) =>
  processTool("JioMartCropper", req, res)
);

// Generic tool route (for any tool name)
app.post("/api/tool/:toolName", upload.array("files", 50), (req, res) => {
  const { toolName } = req.params;
  processTool(toolName, req, res);
});

// --- Enhanced Download route with better logging ---
app.get("/api/:tool/download/:jobId/:filename", (req, res) => {
  const { tool, jobId, filename } = req.params;
  
  try {
    const toolFolder = validateTool(tool);
    
    // Full absolute path (important for AWS/EC2/Render)
    const filePath = path.join(
      process.cwd(),
      "tools",
      toolFolder,
      "output",
      jobId,
      filename
    );

    console.log("📂 Download request:", { tool, jobId, filename, filePath });

    if (!fs.existsSync(filePath)) {
      console.error("❌ File not found:", filePath);
      return res.status(404).json({ error: "File not found", path: filePath });
    }

    // Set appropriate headers
    if (filename.endsWith('.pdf')) {
      res.setHeader('Content-Type', 'application/pdf');
    } else if (filename.endsWith('.xlsx')) {
      res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    }
    
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);

    res.download(filePath, filename, (err) => {
      if (err) {
        console.error("❌ Error during download:", err);
        if (!res.headersSent) {
          res.status(500).json({ error: "Download failed" });
        }
      } else {
        console.log("✅ File sent successfully:", filePath);
      }
    });
  } catch (err) {
    console.error("❌ Download validation error:", err);
    res.status(400).json({ error: err.message });
  }
});

// List available tools
app.get("/api/tools", (req, res) => {
  try {
    const toolsRoot = path.join(process.cwd(), "tools");
    const tools = fs.readdirSync(toolsRoot).filter(item => {
      const itemPath = path.join(toolsRoot, item);
      return fs.statSync(itemPath).isDirectory();
    });
    
    res.json({ success: true, tools });
  } catch (err) {
    console.error("❌ Error listing tools:", err);
    res.status(500).json({ error: "Failed to list tools" });
  }
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
      const toolPath = path.join(toolsRoot, tool);
      if (!(await fsp.stat(toolPath)).isDirectory()) continue;
      
      const outputRoot = path.join(toolPath, "output");
      if (!fs.existsSync(outputRoot)) continue;

      const jobs = await fsp.readdir(outputRoot);
      for (const jobId of jobs) {
        const jobDir = path.join(outputRoot, jobId);
        if (!(await fsp.stat(jobDir)).isDirectory()) continue;

        const files = (await fsp.readdir(jobDir)).filter(
          (f) => f.endsWith(".pdf") || f.endsWith(".xlsx")
        );

        for (const name of files) {
          const filePath = path.join(jobDir, name);
          const stats = await fsp.stat(filePath);

          // reverse map folder name → API route
          const apiTool = Object.keys(TOOL_MAP).find((k) => TOOL_MAP[k] === tool) || tool.toLowerCase();

          allFiles.push({
            tool,
            jobId,
            name,
            size: stats.size,
            modified: stats.mtime,
            url: `/api/${apiTool}/download/${jobId}/${name}`,
          });
        }
      }
    }

    // Sort by modified date (newest first)
    allFiles.sort((a, b) => b.modified - a.modified);
    
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
    const toolFolder = TOOL_MAP[tool.toLowerCase()] || tool;

    const filePath = path.join(process.cwd(), "tools", toolFolder, "output", jobId, filename);

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

// Error handling middleware
app.use((err, req, res, next) => {
  console.error("❌ Unhandled error:", err);
  res.status(500).json({ error: "Internal server error", details: err.message });
});

app.listen(PORT, () => console.log(`✅ Server running at http://localhost:${PORT}`));
