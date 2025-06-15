# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import uuid
import threading
import concurrent.futures
from optimizer import clean_data, run_gurobi_optimizer

app = Flask(__name__)
# CORS allows the React app (on a different port) to call this backend
CORS(app)

# --- CHANGE: Global dictionary for jobs and a lock to make access thread-safe ---
jobs = {}
job_lock = threading.Lock()


def optimization_worker(job_id, stations_df, pair_df, total_audience, current_budget, is_final_run, time_limit, num_points):
    """
    Worker function for a single optimization point. This is executed in a separate thread.
    """
    try:
        result = run_gurobi_optimizer(stations_df, pair_df, total_audience, current_budget, time_limit=time_limit)

        if "error" in result:
            print(f"Warning: Could not solve for budget {current_budget}. Error: {result['error']}")
            # Still update progress to show the task is complete
            with job_lock:
                jobs[job_id]['progress'] += 1 / num_points
            return

        with job_lock:
            # Update the shared reach curve data
            jobs[job_id]['reach_curve'].append({
                "budget": result["total_cost"],
                "reach": result["net_reach_percentage"]
            })
            jobs[job_id]['reach_curve'].sort(key=lambda p: p['budget'])

            # Update progress
            jobs[job_id]['progress'] += 1 / num_points

            # If this is the run for the maximum budget, set its full result as the main one for the UI cards
            if is_final_run:
                jobs[job_id]['main_result'] = result

    except Exception as e:
        print(f"Exception in worker for budget {current_budget}: {e}")
        with job_lock:
            # Mark progress as complete even on error to prevent a stuck progress bar
            jobs[job_id]['progress'] += 1 / num_points


def run_optimization_jobs(job_id, file_content, total_audience, max_budget, sheet_name):
    """
    This function runs in a background thread.
    It prepares data and then launches multiple parallel optimization jobs.
    """
    try:
        # --- 1. Data Prep ---
        with job_lock:
            jobs[job_id]['status'] = 'Preparing data...'

        xls = pd.ExcelFile(file_content)
        # --- CHANGE: Use the user-provided sheet name ---
        if sheet_name not in xls.sheet_names:
            with job_lock:
                jobs[job_id]['status'] = 'Error'
                jobs[job_id]['error'] = f"Sheet '{sheet_name}' not found. Available sheets: {', '.join(xls.sheet_names)}"
            return

        df = pd.read_excel(xls, sheet_name=sheet_name)
        stations_df, pair_df = clean_data(df)

        # --- 2. Incrementally Generate Reach Curve in PARALLEL ---
        with job_lock:
            jobs[job_id]['status'] = 'Generating reach curve...'

        num_points = 10
        budget_points = [(max_budget / num_points) * i for i in range(0, num_points + 1)]

        # --- CHANGE: Use a ThreadPoolExecutor to run all budget optimizations concurrently ---
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_points) as executor:
            # Submit all tasks to the pool. Each task will run `optimization_worker`.
            future_to_budget = {
                executor.submit(
                    optimization_worker,
                    job_id,
                    stations_df,
                    pair_df,
                    total_audience,
                    budget,
                    is_final_run=(i == len(budget_points) - 1),  # True only for the last budget point
                    time_limit=30+(i*15) + (i == len(budget_points) - 1)*30, # Extra 30 for last point
                    num_points=num_points
                ): budget for i, budget in enumerate(budget_points)
            }

            # This loop will check for exceptions that might have occurred in the worker threads
            for future in concurrent.futures.as_completed(future_to_budget):
                budget = future_to_budget[future]
                try:
                    future.result()  # re-raises any exception from the worker
                except Exception as exc:
                    print(f'Job {job_id} task for budget {budget} generated an exception: {exc}')

        with job_lock:
             if jobs[job_id]['status'] != 'Error':
                jobs[job_id]['status'] = 'Completed'

    except Exception as e:
        print(f"Error in main job thread {job_id}: {e}")
        with job_lock:
            jobs[job_id]['status'] = 'Error'
            jobs[job_id]['error'] = f"An unexpected error occurred: {str(e)}"

@app.route('/start-optimization', methods=['POST'])
def start_job():
    # --- Get Inputs ---
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    try:
        total_audience = int(request.form.get('totalAudience'))
        budget = float(request.form.get('budget'))
        # --- CHANGE: Get sheet name from the request ---
        sheet_name = request.form.get('sheetName')
        file = request.files['file']

        if not sheet_name:
            return jsonify({"error": "Sheet Name is required"}), 400

        file_content = file.read()
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid Total Audience or Budget"}), 400

    # --- Create and Start Job ---
    job_id = str(uuid.uuid4())

    # Use the lock to safely add the new job to the dictionary
    with job_lock:
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
        args=(job_id, file_content, total_audience, budget, sheet_name)
    )
    thread.start()

    return jsonify({"job_id": job_id})

@app.route('/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    # Use the lock to safely read from the jobs dictionary
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        # Return a copy to avoid race conditions if the job object is modified while being sent
        return jsonify(job.copy())

if __name__ == '__main__':
    # Run the app on port 5001 to avoid conflicts with React's default port 3000
    app.run(debug=True, port=5001)
