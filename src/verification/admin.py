import json

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User, Group
from django.contrib.sessions.models import Session
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.utils.html import format_html
from django.utils import timezone

from verification.models import Document


# ══════════════════════════════════════════════════════════════════════════════
# Document
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "full_name", "user", "document_type",
        "birth_date", "verified", "ocr_verified", "face_score", "created_at",
    )
    list_filter  = ("verified", "document_type", "created_at")
    search_fields = ("first_name", "last_name", "user__username")
    readonly_fields = (
        "user", "first_name", "last_name", "birth_date", "document_type",
        "doc_file", "selfie_file", "verified", "verification_result",
        "created_at", "doc_preview", "selfie_preview", "result_detail",
    )
    fieldsets = (
        ("Identity", {
            "fields": ("user", "first_name", "last_name", "birth_date", "document_type"),
        }),
        ("Files", {
            "fields": ("doc_file", "doc_preview", "selfie_file", "selfie_preview"),
        }),
        ("Verification result", {
            "fields": ("verified", "result_detail"),
        }),
        ("Metadata", {
            "fields": ("created_at",),
        }),
    )
    ordering = ("-created_at",)

    @admin.display(description="Name")
    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"

    @admin.display(description="OCR", boolean=True)
    def ocr_verified(self, obj):
        if obj.verification_result is None:
            return None
        return obj.verification_result.get("ocr_verified")

    @admin.display(description="Face score")
    def face_score(self, obj):
        if obj.verification_result is None:
            return "—"
        score = obj.verification_result.get("face_score")
        return f"{score:.3f}" if score is not None else "—"

    @admin.display(description="Document preview")
    def doc_preview(self, obj):
        if obj.doc_file:
            if obj.doc_file.name.lower().endswith(".pdf"):
                return format_html('<a href="{}" target="_blank">Open PDF</a>', obj.doc_file.url)
            return format_html(
                '<img src="{}" style="max-height:300px;max-width:100%;" />', obj.doc_file.url
            )
        return "—"

    @admin.display(description="Selfie preview")
    def selfie_preview(self, obj):
        if obj.selfie_file:
            return format_html(
                '<img src="{}" style="max-height:300px;max-width:100%;" />', obj.selfie_file.url
            )
        return "—"

    @admin.display(description="Full result")
    def result_detail(self, obj):
        if obj.verification_result is None:
            return "Pending"
        data = json.dumps(obj.verification_result, indent=2, ensure_ascii=False)
        return format_html("<pre style='margin:0'>{}</pre>", data)


# ══════════════════════════════════════════════════════════════════════════════
# User  (extend built-in UserAdmin with a Documents inline)
# ══════════════════════════════════════════════════════════════════════════════

class DocumentInline(admin.TabularInline):
    model  = Document
    extra  = 0
    fields = ("id", "document_type", "birth_date", "verified", "created_at")
    readonly_fields = ("id", "document_type", "birth_date", "verified", "created_at")
    show_change_link = True
    can_delete = False


admin.site.unregister(User)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = BaseUserAdmin.inlines + (DocumentInline,)
    list_display  = ("username", "email", "first_name", "last_name", "is_staff", "document_count", "date_joined")
    list_filter   = BaseUserAdmin.list_filter + ("date_joined",)
    search_fields = BaseUserAdmin.search_fields

    @admin.display(description="Documents")
    def document_count(self, obj):
        count = obj.document_set.count()
        return count or "—"


# ══════════════════════════════════════════════════════════════════════════════
# Session
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display  = ("session_key", "username", "expire_date", "is_expired")
    readonly_fields = ("session_key", "expire_date", "decoded_data")
    search_fields = ("session_key",)
    ordering      = ("-expire_date",)

    @admin.display(description="User")
    def username(self, obj):
        data = obj.get_decoded()
        uid  = data.get("_auth_user_id")
        if uid:
            try:
                return User.objects.get(pk=uid).username
            except User.DoesNotExist:
                return f"uid:{uid}"
        return "Anonymous"

    @admin.display(description="Expired", boolean=True)
    def is_expired(self, obj):
        return obj.expire_date < timezone.now()

    @admin.display(description="Decoded data")
    def decoded_data(self, obj):
        data = json.dumps(obj.get_decoded(), indent=2, default=str)
        return format_html("<pre style='margin:0'>{}</pre>", data)


# ══════════════════════════════════════════════════════════════════════════════
# ContentType
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(ContentType)
class ContentTypeAdmin(admin.ModelAdmin):
    list_display  = ("app_label", "model")
    list_filter   = ("app_label",)
    search_fields = ("app_label", "model")
    ordering      = ("app_label", "model")


# ══════════════════════════════════════════════════════════════════════════════
# LogEntry  (full admin activity log)
# ══════════════════════════════════════════════════════════════════════════════

ACTION_ICONS = {ADDITION: "➕", CHANGE: "✏️", DELETION: "🗑️"}

@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display  = ("action_time", "user", "action_icon", "content_type", "object_repr", "change_message_short")
    list_filter   = ("action_flag", "content_type", "action_time")
    search_fields = ("user__username", "object_repr", "change_message")
    readonly_fields = (
        "action_time", "user", "content_type", "object_id",
        "object_repr", "action_flag", "change_message",
    )
    ordering = ("-action_time",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Action")
    def action_icon(self, obj):
        return ACTION_ICONS.get(obj.action_flag, "?")

    @admin.display(description="Change")
    def change_message_short(self, obj):
        msg = obj.get_change_message()
        return msg[:80] + "…" if len(msg) > 80 else msg
