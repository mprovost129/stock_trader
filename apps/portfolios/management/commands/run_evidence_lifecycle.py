
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.portfolios.services import run_evidence_lifecycle_automation


class Command(BaseCommand):
    help = "Run evidence lifecycle automation for one user or all users."

    def add_arguments(self, parser):
        parser.add_argument('--username', help='Single username to process')
        parser.add_argument('--archive-expired', action='store_true', help='Archive expired attachments after scanning')

    def handle(self, *args, **options):
        User = get_user_model()
        qs = User.objects.all().order_by('id')
        if options.get('username'):
            qs = qs.filter(username=options['username'])
            if not qs.exists():
                raise CommandError(f"Unknown user: {options['username']}")
        for user in qs:
            result = run_evidence_lifecycle_automation(user=user, archive_expired=options.get('archive_expired', False))
            self.stdout.write(self.style.SUCCESS(
                f"{user.username}: attachments={result['attachment_count']} expiring={result['expiring_soon_count']} expired={result['expired_count']} missing={result['missing_retention_count']} archived={result['archived_count']}"
            ))
