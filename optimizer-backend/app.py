# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import uuid
import threading
from optimizer import clean_data, run_gurobi_optimizer

app = Flask(__name__)
# CORS allows the React app (on a different port) to call this backend
CORS(app)

jobs = {}

def run_optimization_jobs(job_id, file_content, total_audience, max_budget):
    """
    This function runs in a background thread.
    It computes the reach curve points incrementally and uses the last point as the main result.
    """
    try:
        # --- 1. Data Prep ---
        jobs[job_id]['status'] = 'Preparing data...'
        xls = pd.ExcelFile(file_content)
        sheet_name = next((name for name in xls.sheet_names if 'raleigh' in name.lower() and '7' in name), None)
        if not sheet_name:
            jobs[job_id]['status'] = 'Error'
            jobs[job_id]['error'] = "Could not find a sheet named like 'Raleigh 7 day'"
            return

        df = pd.read_excel(xls, sheet_name=sheet_name)
        stations_df, pair_df = clean_data(df)

        # --- 2. Incrementally Generate Reach Curve and Final Result ---
        jobs[job_id]['status'] = 'Generating reach curve...'
        num_points = 10

        for i in range(1, num_points + 1):
            current_budget = (max_budget / num_points) * i

            # For the final point, we can allow a slightly longer time limit for better accuracy.
            time_limit = 60 if i == num_points else 30

            result = run_gurobi_optimizer(stations_df, pair_df, total_audience, current_budget, time_limit=time_limit)

            if "error" in result:
                # If one point fails, we can note it and continue or stop. Let's continue.
                print(f"Warning: Could not solve for budget {current_budget}. Error: {result['error']}")
                continue

            # This is the "streaming" part. Append the result as soon as it's ready.
            # The next poll from the frontend will pick up this new point.
            jobs[job_id]['reach_curve'].append({
                "budget": result["total_cost"],
                "reach": result["net_reach_percentage"]
            })
            jobs[job_id]['reach_curve'].sort(key=lambda p: p['budget'])

            # Update progress for the UI
            jobs[job_id]['progress'] = i / num_points

            # *** KEY CHANGE ***
            # If this is the last point (i.e., for the max budget), use its full data
            # as the main result for the summary cards.
            if i == num_points:
                jobs[job_id]['main_result'] = result

        jobs[job_id]['status'] = 'Completed'

    except Exception as e:
        print(f"Error in job {job_id}: {e}")
        jobs[job_id]['status'] = 'Error'
        jobs[job_id]['error'] = f"An unexpected error occurred: {str(e)}"


@app.route('/start-optimization', methods=['POST'])
def start_job():
    # --- Get Inputs ---
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    # ... (similar input validation as before) ...
    try:
        total_audience = int(request.form.get('totalAudience'))
        budget = float(request.form.get('budget'))
        file = request.files['file']
        file_content = file.read() # Read file content into memory
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid Total Audience or Budget"}), 400

    # --- Create and Start Job ---
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'Pending',
        'main_result': None,
        'reach_curve': [],
        'progress': 0,
        'error': None
    }

    # Run the long process in a background thread
    thread = threading.Thread(
        target=run_optimization_jobs,
        args=(job_id, file_content, total_audience, budget)
    )
    thread.start()

    return jsonify({"job_id": job_id})

@app.route('/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    return jsonify(job)



if __name__ == '__main__':
    # Run the app on port 5001 to avoid conflicts with React's default port 3000
    app.run(debug=True, port=5001)
