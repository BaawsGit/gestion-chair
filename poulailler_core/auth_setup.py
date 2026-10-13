from django.contrib.auth import get_user_model


def ensure_default_accounts():
    """
    Ensure the admin and read-only viewer accounts exist with the required credentials.
    - admin / Admin123! : Full management rights (superuser + staff).
    - user / user123!   : Read-only consultation account (no staff, no superuser).
    """
    User = get_user_model()

    # 1. Compte administrateur : droits totaux
    admin_user, _ = User.objects.get_or_create(username="admin")
    admin_user.set_password("Admin123!")
    admin_user.is_staff = True
    admin_user.is_superuser = True
    admin_user.save()

    # 2. Compte invité / consultation seule : lecture uniquement
    viewer_user, _ = User.objects.get_or_create(username="user")
    viewer_user.set_password("user123!")
    viewer_user.is_staff = False
    viewer_user.is_superuser = False
    viewer_user.save()
