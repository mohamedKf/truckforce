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
    path('auth/driver/change-password/',  views.DriverChangePasswordView.as_view()),

    # ── Company Settings ──────────────────────────────
    path('settings/',                     views.CompanySettingsView.as_view()),

    # ── Managers ──────────────────────────────────────
    path('managers/',                     views.ManagerListCreateView.as_view()),
    path('managers/<int:pk>/',            views.ManagerDetailView.as_view()),

    # ── Drivers — specific before <int:pk> ────────────
    path('drivers/',                          views.DriverListCreateView.as_view()),
    path('drivers/fcm/',                      views.DriverUpdateFCMView.as_view()),
    path('drivers/active-locations/',         views.ActiveDriversLocationsView.as_view()),
    path('drivers/<int:driver_id>/children/', views.DriverChildrenView.as_view()),
    path('driver/photo/',                     views.DriverPhotoUploadView.as_view()),  # driver uploads own photo (uses token, no pk)
    path('drivers/<int:pk>/photo/clear-b64/', views.DriverPhotoClearB64View.as_view()),
    path('drivers/<int:pk>/ping/',            views.DriverPingView.as_view()),
    path('drivers/<int:pk>/',                 views.DriverDetailView.as_view()),

    # ── Driver children ───────────────────────────────
    path('children/<int:pk>/',            views.ChildDetailView.as_view()),

    # ── Trucks ────────────────────────────────────────
    path('trucks/',                       views.TruckListCreateView.as_view()),
    path('trucks/<int:pk>/',              views.TruckDetailView.as_view()),

    # ── Schedules — specific before <int:pk> ──────────
    path('schedules/',                                  views.ScheduleListCreateView.as_view()),
    path('schedules/self/',                             views.DriverSelfScheduleView.as_view()),
    path('schedules/today/',                            views.DriverTodayScheduleView.as_view()),
    path('schedules/by-date/<str:date>/',               views.DriverScheduleByDateView.as_view()),
    path('schedules/<int:pk>/reorder-stops/',           views.DriverReorderStopsView.as_view()),
    path('schedules/<int:pk>/optimize/',                views.OptimizeRouteView.as_view()),
    path('schedules/<int:pk>/apply-suggestion/',        views.ApplyRouteSuggestionView.as_view()),
    path('schedules/<int:pk>/summary/',                 views.ScheduleSummaryView.as_view()),
    path('schedules/<int:schedule_id>/stops/',          views.ScheduleStopsAddView.as_view()),
    path('schedules/<int:pk>/',                         views.ScheduleDetailView.as_view()),

    # ── Stops ─────────────────────────────────────────
    path('stops/<int:pk>/update/',        views.StopUpdateView.as_view()),       # driver: status/skip
    path('stops/<int:pk>/complete/',      views.StopCompleteView.as_view()),     # driver: detailed done/skipped
    path('stops/<int:stop_id>/signature/', views.StopSignatureView.as_view()),    # driver: signature → DeliveryConfirmation
    path('stops/<int:stop_id>/sign/',      views.StopSignatureView.as_view()),    # alias: legacy mobile builds use /sign/
    path('stops/<int:pk>/tasks/',         views.StopTaskListCreateView.as_view()),  # GET/POST tasks
    path('stops/<int:stop_id>/photos/',   views.StopPhotoListCreateView.as_view()),
    path('stops/<int:pk>/',               views.StopDetailView.as_view()),       # manager: edit/delete
    path('stops/<int:pk>/eta-distance/',  views.StopETADistanceView.as_view()),
    path('stop-photos/<int:pk>/',         views.StopPhotoDeleteView.as_view()),
    path('stop-tasks/<int:pk>/',          views.StopTaskDeleteView.as_view()),
    path('stops/<int:pk>/documents/',        views.StopDocumentListCreateView.as_view()),
    path('stop-documents/<int:pk>/sign/',    views.StopDocumentSignView.as_view()),
    path('stop-documents/<int:pk>/',         views.StopDocumentDeleteView.as_view()),
    path('schedules/<int:pk>/delivery-sheet/',      views.DeliverySheetView.as_view()),
    path('schedules/<int:pk>/delivery-sheet/sign/', views.DeliverySheetSignView.as_view()),
    path('stops/<int:pk>/delivery-note/', views.StopDeliveryNoteView.as_view()),
    path('drivers/locations-history/', views.DriverLocationsHistoryView.as_view()),

    # ── Location link → coords ────────────────────────
    path('parse-location/',                      views.ParseLocationLinkView.as_view()),
    path('places/autocomplete/',                 views.PlacesAutocompleteView.as_view()),
    path('places/details/',                      views.PlaceDetailsView.as_view()),
    path('places/resolve/',                      views.PlaceResolveView.as_view()),

    # ── Packages (package_delivery stops) ─────────────
    path('stops/<int:stop_id>/packages/',        views.StopPackagesView.as_view()),
    path('schedules/<int:schedule_id>/packages/', views.SchedulePackagesView.as_view()),
    path('packages/leftovers/',                  views.LeftoverPackagesView.as_view()),
    path('packages/<int:pk>/',                   views.PackageDetailView.as_view()),

    # ── Attendance — specific before <int:pk> ─────────
    path('attendance/',                   views.AttendanceListView.as_view()),
    path('attendance/clock-in/',          views.ClockInView.as_view()),
    path('attendance/clock-out/',         views.ClockOutView.as_view()),
    path('attendance/zero-shifts/',       views.ZeroShiftListView.as_view()),
    path('attendance/<int:pk>/',          views.AttendanceDetailView.as_view()),
    path('attendance/<int:pk>/manual-close/', views.AttendanceManualCloseView.as_view()),

    # ── Attendance fix requests ───────────────────────
    path('attendance-fix-requests/',                 views.AttendanceFixRequestListCreateView.as_view()),
    path('attendance-fix-requests/<int:pk>/decide/', views.AttendanceFixRequestDecideView.as_view()),

    # ── Crane — specific before <int:pk> ──────────────
    path('crane/',                        views.CraneSessionListView.as_view()),
    path('crane/start/',                  views.CraneStartView.as_view()),
    path('crane/<int:pk>/end/',           views.CraneEndView.as_view()),

    # ── Payroll — specific before <int:pk> ────────────
    path('payroll/',                      views.PayrollListView.as_view()),
    path('payroll/generate/',             views.PayrollGenerateView.as_view()),
    path('payroll/<int:pk>/',             views.PayrollDetailView.as_view()),

    # ── Payroll send log ──────────────────────────────
    path('payroll-sends/',                views.PayrollSendLogListView.as_view()),

    # ── Payroll config (singleton) ────────────────────
    path('payroll-config/',               views.PayrollConfigView.as_view()),

    # ── Payslips ──────────────────────────────────────
    path('payslips/',                     views.PayslipListView.as_view()),
    path('payslips/generate/',            views.PayslipGenerateView.as_view()),
    path('payslips/<int:pk>/',            views.PayslipDetailView.as_view()),
    path('payslips/<int:pk>/upload-pdf/', views.PayslipUploadPDFView.as_view()),

    # ── Accountants ───────────────────────────────────
    path('accountants/',                  views.AccountantListCreateView.as_view()),
    path('accountants/<int:pk>/',         views.AccountantDetailView.as_view()),

    # ── Notifications ─────────────────────────────────
    path('notifications/',                views.NotificationListView.as_view()),

    # ── Documents ─────────────────────────────────────
    path('documents/',                    views.DocumentListCreateView.as_view()),
    path('documents/<int:pk>/',           views.DocumentDetailView.as_view()),

    # ── Dashboard ─────────────────────────────────────
    path('dashboard/',                    views.DashboardStatsView.as_view()),

    # ── Live tracking (driver GPS feed) ───────────────
    path('driver/location/',              views.DriverLocationUpdateView.as_view()),
    path('locations/nearest-driver/',     views.NearestDriverView.as_view()),

    # ── Public tracking links (client-facing) ─────────
    # NOTE: tracking_page (HTML) is served from /track/<token>/ at the project urls.py
    # level — see truckforce_backend/urls.py. The API endpoints below live under /api/.
    path('tracking-links/share/',            views.DriverShareTrackingView.as_view()),
    path('tracking-links/',                  views.TrackingLinkListCreateView.as_view()),
    path('tracking-links/<int:pk>/revoke/',  views.TrackingLinkRevokeView.as_view()),
    path('track/<str:token>/data/',          views.tracking_data),
    path('track/<str:token>/eta/',           views.RouteETAView.as_view()),
    path('track/<str:token>/client-note/',   views.ClientNoteView.as_view()),
    path('track/<str:token>/share-location/', views.ShareLocationView.as_view()),

    # ── Desktop auto-updater ──────────────────────────
    path('version/',                      views.app_version),
    path('downloads/<str:filename>',      views.download_release),
    path('upload-release/',               views.UploadReleaseView.as_view()),

    # ── Invoicing module (paid add-on; gated server-side) ─────────────
    path('billing/clients/',                 views.ClientListCreateView.as_view()),
    path('billing/clients/<int:pk>/',        views.ClientDetailView.as_view()),
    path('billing/invoices/',                views.InvoiceListCreateView.as_view()),
    path('billing/invoices/<int:pk>/issue/', views.InvoiceIssueView.as_view()),
    path('billing/invoices/<int:pk>/',       views.InvoiceDetailView.as_view()),
    path('billing/finance-docs/',            views.FinanceDocumentListCreateView.as_view()),
    path('billing/finance-docs/<int:pk>/',   views.FinanceDocumentDeleteView.as_view()),
    path('billing/finance-docs/export-pdf/', views.FinanceExportPDFView.as_view()),
    path('billing/scan-qr/',                 views.ScanQRView.as_view()),
    path('scan/<str:token>/',                views.scan_page_view),
    path('scan/<str:token>/upload/',         views.ScanUploadView.as_view()),
]