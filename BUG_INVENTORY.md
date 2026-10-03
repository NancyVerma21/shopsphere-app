# ShopSphere — Intentionally Buggy AUT Inventory

This is a single SDET Application Under Test. The defects below are intentionally present and are **not** to be fixed during the defect-detection phase.

| ID | Area | Defect | Severity |
|---|---|---|---|
| BUG-001 | Payment | Failed Card/UPI payment still creates an order | Critical |
| BUG-002 | Cart/Inventory | Cart quantity can exceed available stock | High |
| BUG-003 | Products | Price sorting directions are reversed | Medium |
| BUG-004 | Search | Product-name text is not included in search query | Medium |
| BUG-005 | Checkout | Exactly ₹1,000 is charged shipping instead of receiving free shipping | Medium |
| BUG-006 | Orders UI | Cancel action remains visible for a cancelled order | Medium |
| BUG-007 | Authentication | Inactive user can log in | High |
| BUG-008 | Session/Security | Logout does not invalidate the authenticated session | Critical |
| BUG-009 | Authorization | Normal customer can access `/admin` dashboard | Critical |
| BUG-010 | Registration | Passwords with only 4–7 characters are accepted | Medium |
| BUG-011 | Cart | Quantity 0 can be submitted instead of being rejected | Medium |
| BUG-012 | Authorization/Privacy | Order details can be accessed by another authenticated user who knows the order number | Critical |
| BUG-013 | Authorization/Privacy | Order confirmation can be accessed by another authenticated user who knows the order number | Critical |
| BUG-014 | Authorization/Integrity | A user can cancel another user's order when the order number is known | Critical |
| BUG-015 | Inventory | Cancelling an already-cancelled order restores stock again | High |
| BUG-016 | Payment/Database | Payment amount stores subtotal instead of final order total | High |
| BUG-017 | Order Data | `order_items.line_total` stores unit price even when quantity > 1 | Medium |
| BUG-018 | Inventory/Database | Successful checkout decrements product stock twice | High |
| BUG-019 | Order Data | Stored order total excludes shipping charge | High |

## Project rule

These defects are deliberate AUT defects for the SDET portfolio. The objective is to discover, reproduce, automate, investigate, and document them with screenshots, Playwright traces, logs, API checks, and SQL evidence.
