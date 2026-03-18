from django.core.management.base import BaseCommand, CommandError

from apps.signals.services.presets import PRESETS, apply_preset_to_env, current_policy_snapshot


class Command(BaseCommand):
    help = "Apply a conservative, balanced, or aggressive operator preset to .env"

    def add_arguments(self, parser):
        parser.add_argument("preset", choices=sorted(PRESETS.keys()))

    def handle(self, *args, **options):
        preset = options["preset"]
        if preset not in PRESETS:
            raise CommandError(f"Unknown preset: {preset}")

        path, updated = apply_preset_to_env(preset)
        self.stdout.write(self.style.SUCCESS(f"Applied operator preset '{preset}' to {path}"))
        self.stdout.write("Updated keys:")
        for key in updated:
            self.stdout.write(f"  - {key}={PRESETS[preset][key]}")
        self.stdout.write("")
        self.stdout.write("Restart your terminal / server so Django reloads the new .env values.")
        self.stdout.write("Current in-process settings snapshot (will reflect new values after restart):")
        snapshot = current_policy_snapshot()
        for key, value in snapshot.items():
            self.stdout.write(f"  {key}={value}")
