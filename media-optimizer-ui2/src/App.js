// src/App.js
import React, { useState, useEffect, useRef } from "react";
import axios from "axios";
import { Line } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";
import "./App.css";

// Register Chart.js components we'll be using
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
);

function App() {
  // Input state
  const [file, setFile] = useState(null);
  const [totalAudience, setTotalAudience] = useState(1000000);
  const [budget, setBudget] = useState(50000);
  const [sheetName, setSheetName] = useState("");

  // State for the new asynchronous flow
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState("");
  const [jobProgress, setJobProgress] = useState(0);

  // State for results
  const [results, setResults] = useState(null);
  const [reachCurve, setReachCurve] = useState([]);

  // UI state
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  // Use a ref to store the interval ID so we can clear it correctly
  const intervalRef = useRef(null);

  // This effect hook will run to poll for job status whenever a job ID is set
  useEffect(() => {
    if (jobId) {
      intervalRef.current = setInterval(pollJobStatus, 2000); // Poll every 2 seconds
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [jobId]);

  const pollJobStatus = async () => {
    if (!jobId) return;

    try {
      const response = await axios.get(
        `http://localhost:5001/job-status/${jobId}`,
      );
      const data = response.data;

      setJobStatus(data.status);
      setJobProgress(data.progress);

      if (data.main_result) setResults(data.main_result);
      if (data.reach_curve) setReachCurve(data.reach_curve);

      if (data.status === "Completed" || data.status === "Error") {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
        }
        setJobId(null);
        setIsLoading(false);
        if (data.status === "Error") {
          setError(
            data.error || "An unknown error occurred during optimization.",
          );
        }
      }
    } catch (err) {
      setError(
        "Failed to get job status. The connection to the server may have been lost.",
      );
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      setIsLoading(false);
    }
  };

  const handleOptimize = async () => {
    if (!file || !totalAudience || !budget || !sheetName) {
      setError(
        "Please fill in all fields, select a file, and specify the sheet name.",
      );
      return;
    }

    setIsLoading(true);
    setError("");
    setResults(null);
    setReachCurve([]);
    setJobId(null);
    setJobStatus("Submitting job...");
    setJobProgress(0);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("totalAudience", totalAudience);
    formData.append("budget", budget);
    formData.append("sheetName", sheetName);

    try {
      const response = await axios.post(
        "http://localhost:5001/start-optimization",
        formData,
      );
      setJobId(response.data.job_id);
    } catch (err) {
      setError(
        err.response?.data?.error || "Failed to start the optimization job.",
      );
      setIsLoading(false);
    }
  };

  const formatNumber = (num, decimals = 0) => {
    if (typeof num !== "number" || isNaN(num)) return num;
    return num.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  };

  const chartData = {
    labels: reachCurve.map((p) => formatNumber(p.budget)),
    datasets: [
      {
        label: "Reach %",
        data: reachCurve.map((p) => p.reach),
        borderColor: "#1d6a96",
        backgroundColor: "rgba(29, 106, 150, 0.2)",
        fill: true,
        tension: 0.4,
      },
    ],
  };

  // *** THE ONLY CHANGE IS HERE ***
  const chartOptions = {
    responsive: true,
    plugins: {
      legend: { position: "top" },
      title: { display: true, text: "Reach Curve (Efficient Frontier)" },
    },
    scales: {
      x: {
        title: { display: true, text: "Budget ($)" },
      },
      y: {
        title: { display: true, text: "Net Reach (%)" },
        // Ensure the y-axis starts at 0
        min: 0,
        // Let Chart.js determine the max but suggest adding some space at the top.
        // This is the key to dynamic scaling.
        grace: "10%", // This adds 10% of the data range as padding to the top of the axis.
      },
    },
  };

  return (
    <div className="App">
      <h1>Media Plan Optimizer</h1>
      <p>
        Upload your station data, set your audience size and budget, and get an
        optimized media plan that maximizes deduplicated reach.
      </p>

      <div className="controls">
        <div className="control-item">
          <label htmlFor="totalAudience">Total Audience Size</label>
          <input
            id="totalAudience"
            type="number"
            value={totalAudience}
            onChange={(e) => setTotalAudience(e.target.value)}
            disabled={isLoading}
          />
        </div>
        <div className="control-item">
          <label htmlFor="budget">Total Budget ($)</label>
          <input
            id="budget"
            type="number"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            disabled={isLoading}
          />
        </div>
        <div className="control-item">
          <label htmlFor="file">Station Data (XLSX)</label>
          <input
            id="file"
            type="file"
            accept=".xlsx"
            onChange={(e) => setFile(e.target.files[0])}
            disabled={isLoading}
          />
        </div>
        <div className="control-item">
          <label htmlFor="sheetName">Sheet Name in XLSX</label>
          <input
            id="sheetName"
            type="text"
            placeholder="e.g., Raleigh 7 day"
            value={sheetName}
            onChange={(e) => setSheetName(e.target.value)}
            disabled={isLoading}
          />
        </div>
        <button
          className="optimize-button"
          onClick={handleOptimize}
          disabled={isLoading}
        >
          {isLoading ? "Optimizing..." : "Optimize Plan"}
        </button>
      </div>

      {error && <p className="error-message">{error}</p>}

      {isLoading && (
        <div className="loading-message">
          <p>{jobStatus}</p>
          {jobStatus.includes("curve") && (
            <progress
              value={jobProgress}
              max="1"
              style={{ width: "100%", marginTop: "0.5rem" }}
            ></progress>
          )}
        </div>
      )}

      <div className="results-container">
        {results && (
          <div className="results-grid">
            <div className="summary-card">
              <h3>Executive Summary</h3>
              <div className="summary-metrics">
                <div className="metric">
                  <span className="metric-label">Final Cost</span>
                  <span className="metric-value">
                    ${formatNumber(results.total_cost)}
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Net Reach</span>
                  <span className="metric-value">
                    {formatNumber(results.net_reach_percentage, 2)}%
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Reached Audience</span>
                  <span className="metric-value">
                    {formatNumber(results.net_reach_people)}
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Avg. Frequency</span>
                  <span className="metric-value">
                    {formatNumber(results.avg_frequency, 2)}
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">GRPs</span>
                  <span className="metric-value">
                    {formatNumber(results.grps)}
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Spots</span>
                  <span className="metric-value">{results.plan.length}</span>
                </div>
              </div>
            </div>

            <div className="plan-card">
              <h3>Optimized Media Plan</h3>
              <div style={{ maxHeight: "400px", overflowY: "auto" }}>
                <table className="plan-table">
                  <thead>
                    <tr>
                      <th>Station</th>
                      <th>Cost</th>
                      <th>Cume</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.plan
                      .sort((a, b) => b.Cost - a.Cost)
                      .map((station, index) => (
                        <tr key={index}>
                          <td>{station.Station}</td>
                          <td>${formatNumber(station.Cost)}</td>
                          <td>{formatNumber(station.Cume)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {reachCurve.length > 1 && (
          <div className="chart-card" style={{ marginTop: "2rem" }}>
            <h3>Reach vs. Budget</h3>
            <Line options={chartOptions} data={chartData} />
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
