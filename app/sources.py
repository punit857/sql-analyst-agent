SOURCE_META = {
    "sales": {
        "label": "Sales (e-commerce)",
        "description": "Customers, orders, products and categories for a small online store, 2022 to 2024.",
        "examples": [
            "How many orders were cancelled?",
            "Total revenue from delivered orders by product category",
            "Monthly number of orders in 2024",
            "Top 5 customers by total spend, excluding cancelled and returned orders",
            "Average order value by payment method",
        ],
    },
    "chinook": {
        "label": "Chinook (music store)",
        "description": "Artists, albums, tracks, invoices and customers of a digital music store.",
        "examples": [
            "Top 5 artists by number of albums",
            "Total sales per country, highest first",
            "How many tracks are there?",
            "Which genre has the longest average track length?",
        ],
    },
}



# Short, hand-written definitions added to the SQL prompt. Keep them factual.
BUSINESS_NOTES = {
    "sales": (
        "- orders.status is one of delivered, shipped, cancelled, returned.\n"
        "- Revenue = SUM(order_items.quantity * order_items.unit_price).\n"
        "- Profit = SUM((order_items.unit_price - products.cost) * order_items.quantity).\n"
        "- When the question asks for revenue, sales or profit and does not name an order status, "
        "include only orders with status IN ('delivered', 'shipped'). "
        "If the question names a status, follow the question instead.\n"
        
    ),
    "chinook": (
        "- Track.Milliseconds is the track length in milliseconds.\n"
        "- Sales revenue = SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity); Invoice.Total is the invoice amount."
    ),
}