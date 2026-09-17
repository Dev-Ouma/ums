from rest_framework import serializers


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    role = serializers.CharField()
    is_student = serializers.BooleanField()
    is_faculty = serializers.BooleanField()


class FeeBalanceSerializer(serializers.Serializer):
    total_billed = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    credit = serializers.DecimalField(max_digits=12, decimal_places=2)


class ExamResultSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    exam_name = serializers.CharField()
    course_code = serializers.CharField()
    course_title = serializers.CharField()
    term = serializers.CharField(allow_null=True)
    cat_marks = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    exam_marks = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    marks_obtained = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    grade = serializers.CharField(allow_null=True)
    published_at = serializers.DateTimeField(allow_null=True)
