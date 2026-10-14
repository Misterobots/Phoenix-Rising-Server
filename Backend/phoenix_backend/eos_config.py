"""EOS SDK product configuration (GET /sdk/v1/default, /sdk/v1/product/<id>): which base URL each EOS service
uses. The hostnames stay the public EOS ones; the hosts file sends them to this backend's TLS port. The SDK falls back
to built-in defaults for client tuning that is not listed."""

API = 'https://api.epicgames.dev'
ACCOUNT_PROXY = 'https://egp-idsoc-proxy-prod.ol.epicgames.com'

SERVICES = {
    'EOSAuthService': API,
    'AccountsEpicIdService': API,
    'AccountsService': ACCOUNT_PROXY + '/account',
    'FriendService': ACCOUNT_PROXY + '/friends',
    'FriendEpicIdService': API + '/epic/friends',
    'PresenceService': API + '/epic/presence',
    'MetricsService': API + '/datarouter',
    'StatsAchievementsService': API + '/stats',
    'StatsIngestService': API + '/ingestion/stats',
    'LeaderboardsService': API + '/leaderboards',
    'AntiCheatService': API + '/anticheat',
    'SanctionsService': API + '/sanctions',
    'RTCService': API + '/rtc',
    'LobbyService': API + '/lobby',
    'SessionsService': API + '/matchmaking',
    'MatchmakingService': API + '/epic/matchmaking',
    'CustomInvitesService': API + '/notifications',
    'TitleStorageService': API + '/titlestorage',
    'DataStorageService': API + '/datastorage',
    'ProgressionSnapshotService': API + '/snapshots',
    'ReportsService': API + '/player-reports',
    'ModsService': API + '/mods',
    'KWSService': API + '/kws',
    'InventoryService': API + '/inventory',
    'EcommerceEpicIdService': API + '/epic/ecom',
    'PaymentEpicIdService': API + '/epic/payment',
    'ReceiptValidatorService': API + '/receipt-validator',
    'TextChatFNService': API + '/epic/chat',
}


def product_config():
    return {
        'client': {'BaseService': {'HttpRetryLimit': 4, 'HttpRetryResponseCodes': [429, 503, 504]}},
        'services': {name: {'BaseUrl': url} for name, url in SERVICES.items()},
        'watermark': 1,
    }
