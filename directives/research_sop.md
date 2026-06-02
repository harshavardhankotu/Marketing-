# Research Agent SOP

## Objective
Identify trending products from the input seed list, extract competitor pricing, and determine key performance indicators (KPIs).

## Workflow
1. Read the input seed (URLs or Keywords) from `data/input_links.txt`.
2. For each input, attempt to resolve the product category and baseline price.
3. If automated extraction fails (due to anti-bot measures), fall back to categorized placeholder data based on the URL structure (e.g., fktr.in = Flipkart Electronics/Fashion).
4. Output a structured JSON payload for the Creative Agent.

## Data Schema
Each researched product must contain:
- `id`: Unique identifier
- `original_url`: The seed URL
- `inferred_platform`: Flipkart, Amazon, Myntra, etc.
- `trend_score`: 1-100 (simulated for V1)
- `estimated_price`: In INR
- `competitor_notes`: Brief analysis
