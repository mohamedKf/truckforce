from django.urls import path
from . import views

urlpatterns = [

    # ── Auth ──────────────────────────────────────────
    path('auth/manager/login/',           views.ManagerLoginView.as_view()),
    path('auth/manager/logout/',          views.ManagerLogoutView.as_view()),
    path('auth/manager/verify-code/',     views.VerifyRegistrationCodeView.as_view()),
    path('auth/manager/register/',        views.ManagerRegisterView.as_view()),
    path('auth/driver/login/',            views.DriverLoginView.as_view()),
    path('auth/driver/logout/',           views.DriverLogoutView.as_view()),

    # ── Company Settings ──────────────────────────────
    path('settings/',                     views.CompanySettingsView.as_view()),

    # ── Managers ──────────────────────────────────────
    path('managers/',                     views.ManagerListCreateView.as_view()),
    path('managers/<int:pk>/',            views.ManagerDetailView.as_view()),

    # ── Drivers — specific before <int:pk> ────────────
    path('drivers/',                      views.DriverListCreateView.as_view()),
    path('drivers/fcm/',                  views.DriverUpdateFCMView.as_view()),
    path('drivers/<int:pk>/',             views.DriverDetailView.as_view()),

    # ── Trucks ────────────────────────────────────────
    path('trucks/',                       views.TruckListCreateView.as_view()),
    path('trucks/<int:pk>/',              views.TruckDetailView.as_view()),

    # ── Schedules — specific before <int:pk> ──────────
    path('schedules/',                    views.ScheduleListCreateView.as_view()),
    path('schedules/today/',              views.DriverTodayScheduleView.as_view()),
    path('schedules/by-date/<str:date>/',  views.DriverScheduleByDateView.as_view()),
    path('schedules/<int:pk>/reorder-stops/', views.DriverReorderStopsView.as_view()),
    path('schedules/<int:pk>/',           views.ScheduleDetailView.as_view()),

    # ── Stops ─────────────────────────────────────────
    path('stops/<int:stop_id>/sign/',             views.StopSignatureView.as_view()),
    path('stops/<int:pk>/update/',                views.StopUpdateView.as_view()),     # driver: status/skip
    path('stops/<int:pk>/',                       views.StopDetailView.as_view()),     # manager: edit/delete
    path('schedules/<int:schedule_id>/stops/',    views.ScheduleStopsAddView.as_view()), # manager: add stop
    path('stops/<int:stop_id>/photos/',           views.StopPhotoListCreateView.as_view()),
    path('stop-photos/<int:pk>/',                 views.StopPhotoDeleteView.as_view()),

    # ── Attendance — specific before <int:pk> ─────────
    path('attendance/',                   views.AttendanceListView.as_view()),
    path('attendance/clock-in/',          views.ClockInView.as_view()),
    path('attendance/clock-out/',         views.ClockOutView.as_view()),
    path('attendance/<int:pk>/',          views.AttendanceDetailView.as_view()),

    # ── Crane — specific before <int:pk> ──────────────
    path('crane/',                        views.CraneSessionListView.as_view()),
    path('crane/start/',                  views.CraneStartView.as_view()),
    path('crane/<int:pk>/end/',           views.CraneEndView.as_view()),

    # ── Payroll — specific before <int:pk> ────────────
    path('payroll/',                      views.PayrollListView.as_view()),
    path('payroll/generate/',             views.PayrollGenerateView.as_view()),
    path('payroll/<int:pk>/',             views.PayrollDetailView.as_view()),

    # ── Notifications ─────────────────────────────────
    path('notifications/',                views.NotificationListView.as_view()),

    # ── Documents ─────────────────────────────────────
    path('documents/',                    views.DocumentListCreateView.as_view()),
    path('documents/<int:pk>/',           views.DocumentDetailView.as_view()),

    # ── Dashboard ─────────────────────────────────────
    path('dashboard/',                    views.DashboardStatsView.as_view()),

    # ── Live tracking ─────────────────────────────────
    path('driver/location/', views.DriverLocationUpdateView.as_view()),
    path('drivers/active-locations/', views.ActiveDriversLocationsView.as_view()),

    path('accountants/', views.AccountantListCreateView.as_view()),
    path('accountants/<int:pk>/', views.AccountantDetailView.as_view()),

    # ── Payroll send log ──────────────────────────────
    path('payroll-sends/', views.PayrollSendLogListView.as_view()),

    # ── Payroll config (singleton) ────────────────────
    path('payroll-config/', views.PayrollConfigView.as_view()),

    # ── Driver children ───────────────────────────────
    path('drivers/<int:driver_id>/children/', views.DriverChildrenView.as_view()),
    path('children/<int:pk>/', views.ChildDetailView.as_view()),

    # ── Payslips ──────────────────────────────────────
    path('payslips/', views.PayslipListView.as_view()),
    path('payslips/generate/', views.PayslipGenerateView.as_view()),
    path('payslips/<int:pk>/', views.PayslipDetailView.as_view()),
    # ── Attendance fix requests ──
    path('attendance-fix-requests/',                views.AttendanceFixRequestListCreateView.as_view()),
    path('attendance-fix-requests/<int:pk>/decide/', views.AttendanceFixRequestDecideView.as_view()),

    # ── Driver password change ──
    path('auth/driver/change-password/',            views.DriverChangePasswordView.as_view()),
    path('driver/photo/',                           views.DriverPhotoUploadView.as_view()),
    path('drivers/<int:pk>/clear-photo-b64/',       views.DriverPhotoClearB64View.as_view()),

    path('drivers/<int:pk>/ping/', views.DriverPingView.as_view()),

    path('version/',                  views.app_version),
    path('upload-release/',           views.UploadReleaseView.as_view()),
    path('downloads/<str:filename>',  views.download_release),


]