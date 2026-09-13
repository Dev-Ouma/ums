import re
from django.contrib.auth import get_user_model

class EmailIdentityService:
    @staticmethod
    def normalize(email: str) -> str:
        """Normalize: trim whitespace, strip hidden characters, convert to lowercase."""
        if not email:
            return ""
        # Remove whitespace and zero-width spaces
        email = re.sub(r'[\s\u200b\u200c\u200d\u200e\u200f\ufeff]', '', str(email))
        return email.lower()

    @staticmethod
    def is_available(email: str, ignore_user_id: int = None) -> bool:
        """Authoritative database check across all system entities."""
        normalized_email = EmailIdentityService.normalize(email)
        if not normalized_email:
            return False
            
        User = get_user_model()
        user_query = User.objects.filter(email__iexact=normalized_email)
        
        from university.models import InstitutionalEmail
        inst_query = InstitutionalEmail.objects.filter(address__iexact=normalized_email)
        
        if ignore_user_id is not None:
            user_query = user_query.exclude(pk=ignore_user_id)
            inst_query = inst_query.exclude(user_id=ignore_user_id)
            
        return not (user_query.exists() or inst_query.exists())

    @staticmethod
    def validate_institutional_domain(email: str, role: str) -> bool:
        """Validate institutional domain rules based on role."""
        normalized = EmailIdentityService.normalize(email)
        from accounts.models import Role
        
        if role == Role.FACULTY or role == Role.ADMIN:
            return normalized.endswith("@ums.ac.ke")
        elif role == Role.STUDENT:
            return normalized.endswith("@students.ums.ac.ke")
        return True

    @staticmethod
    def generate_student_email(reg_number: str) -> str:
        """Generate deterministic, collision-free student email."""
        clean_reg = re.sub(r"[^A-Za-z0-9]", "", reg_number).lower()
        base_email = f"{clean_reg}@students.ums.ac.ke"
        
        if EmailIdentityService.is_available(base_email):
            return base_email
            
        # Collision handling (should not happen if reg is unique, but fallback)
        counter = 1
        while True:
            candidate = f"{clean_reg}{counter}@students.ums.ac.ke"
            if EmailIdentityService.is_available(candidate):
                return candidate
            counter += 1

    @staticmethod
    def get_conflict_response(email: str, current_user_email: str = None) -> dict:
        """Format friendly, zero-leakage conflict message."""
        normalized = EmailIdentityService.normalize(email)
        
        if current_user_email and EmailIdentityService.normalize(current_user_email) == normalized:
            return {
                "success": False,
                "error": "This is already your email address."
            }
            
        return {
            "success": False,
            "error": "This email is already in use by another UMS account."
        }
