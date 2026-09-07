---
title: The create-order form should offer only materials with a routing
status: inbox
route: /dashboard/orders
branch: main
tags: [fsmes, design]
---

The New order form lists every material from /masterdata/materials, including
raw materials with no routing. Creating an order for RAW-WATER fails at the
API with a routing error the operator cannot fix from this screen.

Options: a `has_routing` filter (or field) on /masterdata/materials, or the
form fetching /masterdata/routings and offering only their materials. The
second needs no API change but makes the form depend on routing pagination
later.
