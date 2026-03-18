from apps.marketdata.management.commands.ingest_watchlist import Command as IngestWatchlistCommand


class Command(IngestWatchlistCommand):
    help = "Automatic market ingestion engine for a user's watchlist. Alias for ingest_watchlist with backlog prioritization and batching controls."
