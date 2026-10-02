from fastapi import APIRouter

from app.api.v1 import (
    auth,
    calls,
    campaigns,
    companies,
    conversations,
    customers,
    dashboard,
    domains,
    inbound_calls,
    knowledge,
    telephony,
    tickets,
    users,
    webhooks,
)

api_router = APIRouter()
for module in (auth, companies, users, customers, dashboard, domains, knowledge, conversations, inbound_calls, tickets, campaigns, calls,
               webhooks, telephony):
    api_router.include_router(module.router)
api_router.include_router(telephony.audio_router)
api_router.include_router(telephony.inbound_router)
