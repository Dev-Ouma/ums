from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase


class PasswordPolicyTests(TestCase):
    def test_passwords_shorter_than_eight_characters_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_password("short7")

    def test_eight_character_passwords_are_not_rejected_for_length(self):
        validate_password("Longer8!")
