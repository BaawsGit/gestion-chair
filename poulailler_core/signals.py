from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Batch, ProphylaxisProgram, TreatmentLog


def sync_batch_treatments(batch):
    active_program_days = set()
    for program in ProphylaxisProgram.objects.filter(is_active=True):
        target_days = range(
            program.target_day,
            program.target_day + program.duration_days,
            program.frequency_days,
        )
        for target_day in target_days:
            active_program_days.add(target_day)
            treatment, created = TreatmentLog.objects.get_or_create(
                batch=batch,
                target_day=target_day,
                defaults={
                    "program": program,
                    "date_planned": batch.start_date + timedelta(days=target_day - 1),
                    "medicine_name": program.medicine_name,
                    "dosage": program.dosage,
                    "administration_route": program.administration_route,
                },
            )
            if not created and not treatment.is_completed:
                treatment.program = program
                treatment.date_planned = batch.start_date + timedelta(days=target_day - 1)
                treatment.medicine_name = program.medicine_name
                treatment.dosage = program.dosage
                treatment.administration_route = program.administration_route
                treatment.save(update_fields=[
                    "program", "date_planned", "medicine_name", "dosage", "administration_route"
                ])

    # Clean up uncompleted treatments for days that no longer have a prophylaxis program
    TreatmentLog.objects.filter(
        batch=batch,
        is_completed=False,
        program__isnull=False
    ).exclude(target_day__in=active_program_days).delete()



@receiver(post_save, sender=Batch)
def create_batch_schedule(sender, instance, **kwargs):
    sync_batch_treatments(instance)


@receiver(post_save, sender=ProphylaxisProgram)
def update_active_batch_schedules(sender, instance, **kwargs):
    if instance.is_active:
        for batch in Batch.objects.filter(status=Batch.Status.ACTIVE).iterator():
            sync_batch_treatments(batch)
