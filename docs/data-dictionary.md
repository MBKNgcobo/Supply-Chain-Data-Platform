# Source file column types — REFERENCE ONLY

**This is not the canonical documentation for any dataset.** It documents
`supply_chain_data.csv` exactly as delivered (the type answers recorded in
SPEC-001 Q7), and it is the authority for the TEXT-to-typed cast boundary in
staging. It describes the *source file*, not any warehouse table.

Canonical documentation lives with the data it describes:

| What | Where |
| --- | --- |
| Dataset columns, descriptions, tests | dbt model YAML (`dbt/models/**`) |
| Grain, keys, quality checks, publish gate, ownership | `docs/contracts/**` |
| Domain terms | `GLOSSARY.md` |
| Open semantic questions | The model YAML descriptions that flag them |

## Type list as delivered (24 columns)

```sql
product_type VARCHAR(50),
sku VARCHAR(20),
price NUMERIC(12,2),
availability INTEGER,
number_of_products_sold INTEGER,
revenue_generated NUMERIC(14,2),
customer_demographics VARCHAR(50),
stock_levels INTEGER,
lead_times INTEGER,
order_quantities INTEGER,
shipping_times INTEGER,
shipping_carriers VARCHAR(50),
shipping_costs NUMERIC(12,2),
supplier_name VARCHAR(50),
location VARCHAR(100),
lead_time INTEGER,
production_volumes INTEGER,
manufacturing_lead_time INTEGER,
manufacturing_costs NUMERIC(12,2),
inspection_results VARCHAR(20),
defect_rates NUMERIC(10,4),
transportation_modes VARCHAR(50),
routes VARCHAR(50),
costs NUMERIC(14,2)
```

Note: `lead_times` and `lead_time` are colliding source headers; they reach
bronze as `lead_times_1` / `lead_times_2` and stay separate until SPEC-001 Q3
is answered.
/*
Product Type: The type of product associated with specific data in the supply chain. (i.e haircare, skincare)
SKU (Stock Keeping Unit): Unique code used to identify a particular product.
Price: The price of the product or item in the supply chain.
Availability: Information about product availability.
Number of Products Sold: The number of products that have been sold in a certain time period.
Revenue Generated: Total revenue generated from product sales in a certain time period.
Customer demographics: Information about customer characteristics, such as age, gender, geographic location, etc. (e.g:- Male, female, Unknown)
Stock Levels: The number of products still available in stock at any given time.
Lead Times: The time required to order or receive products from suppliers.
Order Quantities: The number of products ordered in one order or shipment.(Economic Order Quantity) A negative order quantity is invalid — no encoding is assumed, only sign — and is asserted at gold by dbt test (severity error).
Shipping Times: The time required to ship products from the warehouse or distribution center to customers.
Shipping Carriers: Companies or services used to ship products to customers.
Shipping Costs: Costs associated with shipping products, including delivery fees and additional fees.
Supplier Name: Name of supplier or vendor who provides products or materials to the company.
Location: The physical location associated with the data in the supply chain, such as the location of a warehouse or distribution center.
Lead Time: The time required to obtain products or materials from a particular supplier.
Production Volumes: The number of products produced in a certain time-period.
Manufacturing Lead Time: The time required to produce a product, from ordering materials until the product is ready.
Manufacturing Costs: Costs related to the production process, including raw material costs, labor, etc.
Inspection Results: Results of product or material quality inspection. (e.g:- Pass, Fail, Pending)
Defect Rates: The level of defects or defects in the products produced.
Transportation Modes: The transportation mode used to send products, such as land, sea or air.
Routes: Routes or paths used to send products from one point to another in the supply chain.
Costs: Costs related to various aspects of the supply chain, including transportation, inventory, warehousing, order processing, administration, and other costs.
No time period provided
*/