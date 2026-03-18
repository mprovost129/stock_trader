from django.core.management.base import BaseCommand

from apps.signals.services.presets import PRESETS, current_policy_snapshot


class Command(BaseCommand):
    help = "Show the current operator policy and available presets"

    def handle(self, *args, **options):
        self.stdout.write("Current operator policy\n")
        for key, value in current_policy_snapshot().items():
            self.stdout.write(f"{key}={value}")

        self.stdout.write("\nAvailable presets\n")
        for name, values in PRESETS.items():
            self.stdout.write(f"[{name}]")
            for key, value in values.items():
                self.stdout.write(f"  {key}={value}")
            self.stdout.write("")
