# optimizer.py
import pandas as pd
import numpy as np
import gurobipy as gp
from gurobipy import GRB

def clean_data(df):
    """Cleans the raw dataframe from the XLSX file."""
    # Select and rename core columns for station 1
    df1 = df[['AQH1_Concatenate', 'AQH1_Cost-P18+', 'Cume1']].copy()
    df1.rename(columns={
        'AQH1_Concatenate': 'Station',
        'AQH1_Cost-P18+': 'Cost',
        'Cume1': 'Cume'
    }, inplace=True)

    # Select and rename core columns for station 2
    df2 = df[['AQH2_Concatenate', 'AQH2_Cost-P18+', 'Cume2']].copy()
    df2.rename(columns={
        'AQH2_Concatenate': 'Station',
        'AQH2_Cost-P18+': 'Cost',
        'Cume2': 'Cume'
    }, inplace=True)

    # Combine and get unique stations
    unique_stations_df = pd.concat([df1, df2]).drop_duplicates(subset='Station').reset_index(drop=True)

    # Clean numeric columns
    for col in ['Cost', 'Cume']:
        unique_stations_df[col] = pd.to_numeric(unique_stations_df[col].astype(str).str.replace('[$,]', '', regex=True), errors='coerce')

    unique_stations_df.dropna(inplace=True)
    unique_stations_df = unique_stations_df[unique_stations_df['Cost'] > 0] # Exclude free spots

    # Create a mapping for station pairs and their combined cume
    pair_df = df[['AQH1_Concatenate', 'AQH2_Concatenate', 'Combined Cume']].copy()
    pair_df.rename(columns={
        'AQH1_Concatenate': 'Station1',
        'AQH2_Concatenate': 'Station2'
    }, inplace=True)
    pair_df['Combined Cume'] = pd.to_numeric(pair_df['Combined Cume'].astype(str).str.replace(',', '', regex=True), errors='coerce')

    return unique_stations_df, pair_df

def run_greedy_optimizer(stations_df, budget):
    """
    A simple greedy optimizer that selects stations based on the best Cume-to-Cost ratio.
    This is the initial simple version requested.
    """
    plan = []
    current_cost = 0

    # Calculate efficiency (Cume per dollar)
    df = stations_df.copy()
    df['efficiency'] = df['Cume'] / df['Cost']
    df = df.sort_values(by='efficiency', ascending=False)

    for _, station in df.iterrows():
        if current_cost + station['Cost'] <= budget:
            plan.append(station.to_dict())
            current_cost += station['Cost']

    # Calculate total cume (this is a simple sum, ignores duplication)
    total_cume = sum(s['Cume'] for s in plan)

    return {
        "plan": plan,
        "total_cost": current_cost,
        "total_cume_gross": total_cume,
        "note": "Greedy result is a simple sum of Cume, not deduplicated reach."
    }

def run_gurobi_optimizer(stations_df, pair_df, total_audience, budget,time_limit=60):
    """
    Runs the full quadratic optimizer to maximize deduplicated reach.
    """
    if total_audience <= 0:
        raise ValueError("Total audience must be greater than 0.")

    stations = stations_df['Station'].tolist()
    costs = stations_df.set_index('Station')['Cost'].to_dict()
    cumes = stations_df.set_index('Station')['Cume'].to_dict()

    # --- Pre-computation for the Gurobi model ---
    # r_i: Individual reach probability for each station
    # d_ij: Duplication probability between station i and j

    r_i = {s: cumes[s] / total_audience for s in stations}

    duplication = {}
    for _, row in pair_df.iterrows():
        s1, s2 = row['Station1'], row['Station2']
        if s1 in stations and s2 in stations:
            # Ensure consistent key order (s1, s2)
            if s1 > s2:
                s1, s2 = s2, s1

            # Combined reach R_ij = r_i + r_j - d_ij
            # Therefore, d_ij = r_i + r_j - R_ij
            combined_cume = row['Combined Cume']
            combined_reach_prob = combined_cume / total_audience
            d_ij = r_i[row['Station1']] + r_i[row['Station2']] - combined_reach_prob

            # We only care about positive duplication
            if d_ij > 0:
                duplication[(s1, s2)] = d_ij

    # --- Gurobi Model ---
    model = gp.Model("MediaPlanOptimizer")

    # 1. Decision Variables: x_i = 1 if we buy station i, 0 otherwise
    x = model.addVars(stations, vtype=GRB.BINARY, name="x")

    # 2. Objective Function: Maximize Net Reach
    # Net Reach ≈ Σ r_i * x_i - Σ d_ij * x_i * x_j
    linear_part = gp.quicksum(r_i[s] * x[s] for s in stations)
    quadratic_part = gp.quicksum(duplication[pair] * x[pair[0]] * x[pair[1]] for pair in duplication)

    model.setObjective(linear_part - quadratic_part, GRB.MAXIMIZE)

    # 3. Constraints
    # Budget constraint: Σ cost_i * x_i <= budget
    model.addConstr(gp.quicksum(costs[s] * x[s] for s in stations) <= budget, "Budget")

    # 4. Optimize
    model.setParam('OutputFlag', 0) # Suppress Gurobi console output
    model.optimize()

    # --- Extract Results ---
    if model.status == GRB.OPTIMAL or model.status == GRB.SUBOPTIMAL:
        selected_stations_list = [s for s in stations if x[s].X > 0.5]

        plan = stations_df[stations_df['Station'].isin(selected_stations_list)].to_dict('records')
        total_cost = sum(s['Cost'] for s in plan)

        # Calculate final net reach and other metrics
        net_reach_prob = model.ObjVal
        net_reach_people = net_reach_prob * total_audience
        total_gross_cume = sum(s['Cume'] for s in plan)

        # Gross Rating Points (GRPs)
        grps = (total_gross_cume / total_audience) * 100 if total_audience > 0 else 0

        # Average Frequency = Total Exposures / Net Reach People
        # (Total Gross Cume is our proxy for total exposures)
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
    else:
        return {"error": "Optimizer could not find an optimal solution."}
