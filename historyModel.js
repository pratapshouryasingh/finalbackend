import mongoose from "mongoose";

const historySchema = new mongoose.Schema({
  userId: {
    type: String,
    required: true,
  },
  toolName: {
    type: String,
    required: true,
  },
  jobId: {
    type: String,
    required: true,
  },
  timestamp: {
    type: Date,
    default: Date.now,
  },
  outputs: [
    {
      name: String,
      url: String,
    },
  ],
});

const History = mongoose.model("History", historySchema);

export default History;