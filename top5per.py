import pandas as pd

# Φόρτωση του νέου σου αρχείου
df = pd.read_csv('data/ratings.csv')

# Υπολογισμός του δείκτη Value (Projected PIR ανά Credit)
df['Value'] = df['Projected_PIR'] / df['Credits']

# Εφαρμογή του Κόφτη Σχετικότητας (τουλάχιστον 18 λεπτά συμμετοχής)
valid_df = df[df['Projected_Minutes'] >= 10].copy()

# Εύρεση των Top 5 ανά θέση
for pos in valid_df['Position'].unique():
    top5 = valid_df[valid_df['Position'] == pos].sort_values('Value', ascending=False).head(5)
    print(f"--- Top 5 {pos}s ---")
    print(top5[['Player', 'Team', 'Credits', 'Projected_PIR', 'Value']].to_string(index=False))
    print("\n")