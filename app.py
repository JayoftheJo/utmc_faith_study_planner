from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
import itertools
import os
import tempfile
from collections import defaultdict
import json

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  

FAITH_STUDIES = ["discovery", "source", "growth", "trust", "commission"]

def get_next_faith_study(completed):
    """Determine the next faith study based on completed ones."""
    if pd.isna(completed):
        return FAITH_STUDIES[0]  # If blank, start at Discovery
    completed_lower = [c.strip().lower() for c in str(completed).split(",") if c.strip()]
    for study in FAITH_STUDIES:
        if study not in completed_lower:
            return study
    return None  # already completed all

def has_led(study, led_str):
    """Check if a person already led this study."""
    if pd.isna(led_str):
        return False
    led_lower = [c.strip().lower() for c in str(led_str).split(",") if c.strip()]
    return study in led_lower

def find_common_slots(avail_dicts):
    """Return list of all availability slots common to everyone in the group."""
    if not avail_dicts:
        return []
    
    # For each person, find all time slots they're available
    person_available_slots = []
    for avail in avail_dicts:
        available_slots = []
        for slot, value in avail.items():
            if pd.notna(value) and str(value).strip():
                # Check if the value contains day names (like "Fridays", "Mondays, Wednesdays")
                value_str = str(value).strip()
                if any(day in value_str.lower() for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']):
                    available_slots.append(slot)
        person_available_slots.append(set(available_slots))
    
    if not person_available_slots:
        return []
    
    # Find intersection of all available slots
    common_slots = set.intersection(*person_available_slots)
    return sorted(list(common_slots))

def process_csv_data(df):
    """Process the CSV data and return people and groups."""
    # Identify availability columns (everything after "Faith Studies Led")
    try:
        led_col_index = df.columns.get_loc("Please indicate which faith studies you have led:")
        availability_cols = df.columns[led_col_index+1:]
    except KeyError:
        # look for columns containing time slots
        availability_cols = [col for col in df.columns if 'timeslot' in col.lower() or '[' in col and ']' in col]
    
    people = []
    for _, row in df.iterrows():
        next_study = get_next_faith_study(row.get("Please indicate which faith studies you've completed.", ""))
        person = {
            "id": f"{row.get('First Name', '')}_{row.get('Last Name', '')}_{len(people)}",
            "first": row.get("First Name", ""),
            "last": row.get("Last Name", ""),
            "gender": row.get("Please indicate your gender.", "").strip().lower(),
            "email": row.get("E-mail Address", ""),
            "phone": row.get("Cell Phone Number", ""),
            "year": row.get("What year of study are you currently in?", ""),
            "program": row.get("What is your program of study?", ""),
            "religion": row.get("Which religion/faith do you most identify with?", ""),
            "next_study": next_study,
            "willing_lead": str(row.get("Are you willing to lead a Faith Study?", "")).strip().lower() == "yes",
            "already_led": row.get("Please indicate which faith studies you have led:", ""),
            "avail": {col: str(row[col]).strip() for col in availability_cols}
        }
        people.append(person)

    # Group people by gender + next faith study
    grouped = defaultdict(list)
    for p in people:
        # Only include people who have a next study and valid gender
        if p["next_study"] and p["gender"] and p["gender"].strip():
            grouped[(p["gender"], p["next_study"])].append(p)

    results = []
    group_counter = 1

    for (gender, study), members in grouped.items():
        # Generate groups of 2–5 people
        for size in range(5, 1, -1):  # try larger groups first
            for combo in itertools.combinations(members, size):
                combo = list(combo)
                common = find_common_slots([m["avail"] for m in combo])
                if common:  # only valid if they share a slot
                    leader = None
                    for m in combo:
                        if m["willing_lead"] and not has_led(study, m["already_led"]):
                            leader = m["id"]
                            break

                    group_data = {
                        "id": f"G{group_counter}",
                        "faith_study": study.capitalize(),
                        "gender": gender.capitalize(),
                        "leader": leader,
                        "members": [m["id"] for m in combo],
                        "common_availabilities": common,
                        "member_details": {m["id"]: {
                            "name": f"{m['first']} {m['last']}",
                            "email": m["email"],
                            "phone": m["phone"],
                            "year": m["year"],
                            "program": m["program"]
                        } for m in combo}
                    }
                    results.append(group_data)
                    group_counter += 1
                    break  # Only take the first valid group of this size

    return results, people

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    print(f"Upload request received. Files: {list(request.files.keys())}")
    
    if 'file' not in request.files:
        print("No 'file' key in request.files")
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    print(f"File received: {file.filename}, size: {file.content_length}")
    
    if file.filename == '':
        print("Empty filename")
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Read the uploaded file
        print(f"Attempting to read file: {file.filename}")
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
            print(f"CSV read successfully. Shape: {df.shape}")
        elif file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
            print(f"Excel read successfully. Shape: {df.shape}")
        else:
            print(f"Unsupported file format: {file.filename}")
            return jsonify({'error': 'Unsupported file format. Please upload CSV or Excel files.'}), 400
        
        # Check for required columns
        print(f"DataFrame columns: {list(df.columns)}")
        required_columns = [
            "First Name", "Last Name", "Please indicate your gender.",
            "Please indicate which faith studies you've completed.",
            "Are you willing to lead a Faith Study?"
        ]
        
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"Missing columns: {missing_columns}")
            return jsonify({'error': f'Missing required columns: {", ".join(missing_columns)}'}), 400
        
        # Process the data
        groups, people = process_csv_data(df)
        
        if not people:
            return jsonify({'error': 'No valid people found in the data'}), 400
        
        return jsonify({
            'success': True,
            'groups': groups,
            'people': people,
            'total_people': len(people),
            'total_groups': len(groups)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route('/validate_move', methods=['POST'])
def validate_move():
    data = request.json
    person_id = data.get('person_id')
    from_group_id = data.get('from_group_id')
    to_group_id = data.get('to_group_id')
    groups = data.get('groups')
    people = data.get('people')
    
    # Find the person and groups
    person = next((p for p in people if p['id'] == person_id), None)
    from_group = next((g for g in groups if g['id'] == from_group_id), None)
    to_group = next((g for g in groups if g['id'] == to_group_id), None)
    
    if not person or not from_group or not to_group:
        return jsonify({'valid': False, 'reason': 'Person or group not found'})
    
    # Check if person can be moved to the target group
    # 1. Same gender and faith study
    if (person['gender'] != to_group['gender'].lower() or 
        person['next_study'] != to_group['faith_study'].lower()):
        return jsonify({'valid': False, 'reason': 'Gender or faith study mismatch'})
    
    # 2. Check group size limits
    if len(to_group['members']) >= 5:
        return jsonify({'valid': False, 'reason': 'Target group is full (max 5 people)'})
    
    if len(from_group['members']) <= 2:
        return jsonify({'valid': False, 'reason': 'Source group would be too small (min 2 people)'})
    
    # 3. Check availability compatibility
    to_group_members = [p for p in people if p['id'] in to_group['members']]
    to_group_members.append(person)
    common_slots = find_common_slots([m['avail'] for m in to_group_members])
    
    if not common_slots:
        return jsonify({'valid': False, 'reason': 'No common availability slots'})
    
    return jsonify({'valid': True, 'common_slots': common_slots})

@app.route('/export', methods=['POST'])
def export_groups():
    data = request.json
    groups = data.get('groups')
    
    # Convert to export format
    export_data = []
    for group in groups:
        leader_name = "None"
        if group['leader']:
            leader_details = group['member_details'].get(group['leader'], {})
            leader_name = leader_details.get('name', 'Unknown')
        
        member_names = [group['member_details'][member_id]['name'] 
                       for member_id in group['members'] 
                       if member_id in group['member_details']]
        
        export_data.append({
            'Group ID': group['id'],
            'Faith Study': group['faith_study'],
            'Gender': group['gender'],
            'Leader': leader_name,
            'Members': ', '.join(member_names),
            'Common Availabilities': ', '.join(group['common_availabilities'])
        })
    
    # Create temporary CSV file
    df = pd.DataFrame(export_data)
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    df.to_csv(temp_file.name, index=False)
    temp_file.close()
    
    return send_file(temp_file.name, as_attachment=True, 
                    download_name='faith_study_groups.csv',
                    mimetype='text/csv')

@app.route('/debug')
def debug():
    """Debug endpoint to test with sample data"""
    try:
        df = pd.read_csv("Winter 2025 UTM Faith Study Sign Up (Responses) - Form Responses 1.csv")
        groups, people = process_csv_data(df)
        return jsonify({
            'success': True,
            'groups': groups[:3],  # First 3 groups for debugging
            'people': people[:5],  # First 5 people for debugging
            'total_people': len(people),
            'total_groups': len(groups),
            'columns': list(df.columns)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': str(e)})

if __name__ == '__main__':
    app.run(debug=True)
