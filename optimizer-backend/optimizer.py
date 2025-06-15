# optimizer.py
import pandas as pd
import numpy as np
import gurobipy as gp
from gurobipy import GRB

def clean_data(df):
    """Cleans the raw dataframe from the XLSX file."""
    # This function remains unchanged as it correctly prepares the data.
    df1 = df[['AQH1_Concatenate', 'AQH1_Cost-P18+', 'Cume1']].copy()
    df1.rename(columns={
        'AQH1_Concatenate': 'Station',
        'AQH1_Cost-P18+': 'Cost',
        'Cume1': 'Cume'
    }, inplace=True)

    df2 = df[['AQH2_Concatenate', 'AQH2_Cost-P18+', 'Cume2']].copy()
    df2.rename(columns={
        'AQH2_Concatenate': 'Station',
        'AQH2_Cost-P18+': 'Cost',
        'Cume2': 'Cume'
    }, inplace=True)

    unique_stations_df = pd.concat([df1, df2]).drop_duplicates(subset='Station').reset_index(drop=True)

    for col in ['Cost', 'Cume']:
        unique_stations_df[col] = pd.to_numeric(unique_stations_df[col].astype(str).str.replace('[$,]', '', regex=True), errors='coerce')

    unique_stations_df.dropna(inplace=True)
    unique_stations_df = unique_stations_df[unique_stations_df['Cost'] > 0]

    pair_df = df[['AQH1_Concatenate', 'AQH2_Concatenate', 'Combined Cume']].copy()
    pair_df.rename(columns={
        'AQH1_Concatenate': 'Station1',
        'AQH2_Concatenate': 'Station2'
    }, inplace=True)
    pair_df['Combined Cume'] = pd.to_numeric(pair_df['Combined Cume'].astype(str).str.replace(',', '', regex=True), errors='coerce')
    pair_df.dropna(inplace=True)

    return unique_stations_df, pair_df

def run_gurobi_optimizer(stations_df, pair_df, total_audience, budget, time_limit=60):
    """
    Runs the quadratic optimizer based on a second-order Poisson reach approximation.
    This implementation calibrates a correlated Poisson model to the provided Cume data
    to create a robust quadratic objective function for Gurobi.
    """
    if total_audience <= 0:
        return {"error": "Total Audience must be a positive number."}

    stations = stations_df['Station'].tolist()
    costs = stations_df.set_index('Station')['Cost'].to_dict()
    cumes = stations_df.set_index('Station')['Cume'].to_dict()
    epsilon = 1e-9  # A small number to prevent log(0) for stations with 100% reach

    # --- ALGORITHM STEP 1: Calculate Individual Reach (rᵢ) and Mean Exposures (λᵢ) ---
    # rᵢ = 1 - e^(-λᵢ)  =>  λᵢ = -ln(1 - rᵢ)

    r_i = {s: cumes[s] / total_audience for s in stations}
    # Ensure reach is not >= 1, which would lead to infinite lambda
    for s in r_i:
        if r_i[s] >= 1.0:
            r_i[s] = 1.0 - epsilon

    lambdas = {s: -np.log(1 - r_i[s]) for s in stations}

    # --- ALGORITHM STEP 2: Derive Covariance (λ_Wᵢⱼ) and Final Duplication (dᵢⱼ) ---
    # We calibrate the model's covariance term (λ_Wᵢⱼ) to match the empirical
    # duplication observed in the data.

    d_ij = {}
    for _, row in pair_df.iterrows():
        s1_name, s2_name = row['Station1'], row['Station2']

        # Ensure both stations in the pair exist in our main station list
        if s1_name in stations and s2_name in stations:
            # For consistent dictionary keys, always order station names alphabetically
            s1, s2 = min(s1_name, s2_name), max(s1_name, s2_name)

            # Get individual reach probabilities and lambdas for the pair
            r1, r2 = r_i[s1], r_i[s2]
            lambda1, lambda2 = lambdas[s1], lambdas[s2]

            # Calculate the combined reach probability from the data
            R12 = row['Combined Cume'] / total_audience

            # --- Derive the covariance term λ_Wᵢⱼ ---
            # λ_Wᵢⱼ = λᵢ + λⱼ + ln(1 - rᵢ - rⱼ + Rᵢⱼ)
            log_arg = 1 - r1 - r2 + R12

            lambda_W = 0  # Default to independence if data is inconsistent
            if log_arg > 0:
                lambda_W = lambda1 + lambda2 + np.log(log_arg)
            else:
                # This indicates inconsistent data (e.g., Combined Cume < Cume1).
                # The most robust fallback is to assume independence (covariance = 0).
                print(f"Warning: Inconsistent data for pair ({s1}, {s2}). Combined reach is too low. Assuming independence.")

            # The covariance term must be non-negative in this model.
            lambda_W = max(0, lambda_W)

            # --- Calculate the final model duplication dᵢⱼ for the Gurobi objective ---
            # dᵢⱼ = 1 – e^-(λᵢ + λⱼ − λ_Wᵢⱼ)
            exponent = -(lambda1 + lambda2 - lambda_W)
            duplication_prob = 1 - np.exp(exponent)

            if duplication_prob > 0:
                d_ij[(s1, s2)] = duplication_prob

    # --- ALGORITHM STEP 3: Build and Configure the Gurobi Model ---
    model = gp.Model("MediaPlanOptimizer")

    # 1. Decision Variables: xᵢ = 1 if we buy station i, 0 otherwise
    x = model.addVars(stations, vtype=GRB.BINARY, name="x")

    # 2. Objective Function: Maximize Net Reach (Second-Order Approximation)
    # Net Reach ≈ Σ rᵢ * xᵢ - Σ dᵢⱼ * xᵢ * xⱼ
    linear_part = gp.quicksum(r_i[s] * x[s] for s in stations)
    quadratic_part = gp.quicksum(d_ij[pair] * x[pair[0]] * x[pair[1]] for pair in d_ij)

    model.setObjective(linear_part - quadratic_part, GRB.MAXIMIZE)

    # 3. Constraints
    model.addConstr(gp.quicksum(costs[s] * x[s] for s in stations) <= budget, "Budget")

    # 4. Optimizer Settings
    model.setParam('OutputFlag', 0)  # Suppress Gurobi console output
    model.setParam('TimeLimit', time_limit)
    model.setParam('MIPGap', 0.005) # Target a 0.5% optimality gap

    # --- ALGORITHM STEP 4: Optimize and Extract Results ---
    model.optimize()

    # --- Robustly Handle Different Optimizer Statuses ---
    if model.status in [GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT]:
        # Check if a solution was actually found, especially in case of a time limit
        if model.SolCount == 0:
            return {"error": f"Optimizer timed out after {time_limit}s without finding a feasible solution for the given budget."}

        selected_stations_list = [s for s in stations if x[s].X > 0.5]
        plan = stations_df[stations_df['Station'].isin(selected_stations_list)].to_dict('records')
        total_cost = sum(s['Cost'] for s in plan)

        # Calculate final metrics from the model's objective value
        net_reach_prob = model.ObjVal
        net_reach_people = net_reach_prob * total_audience
        total_gross_cume = sum(s['Cume'] for s in plan)

        grps = (total_gross_cume / total_audience) * 100 if total_audience > 0 else 0
        avg_frequency = total_gross_cume / net_reach_people if net_reach_people > 0 else 0

        return {
            "plan": plan,
            "total_cost": total_cost,
            "net_reach_percentage": net_reach_prob * 100,
            "net_reach_people": net_reach_people,
            "total_gross_cume": total_gross_cume,
            "grps": grps,
            "avg_frequency": avg_frequency,
        }
    elif model.status == GRB.INFEASIBLE:
        return {"error": "The problem is infeasible. This likely means the budget is too low to purchase even the cheapest station."}
    else:
        return {"error": f"Optimizer failed with status code: {model.status}. Please check inputs and budget."}
