class UserError(Exception):
    """Ошибка, понятная пользователю: показываем её текст в интерфейсе как есть."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(UserError):
    pass


class ConflictError(UserError):
    """Данные в Keitaro изменились после нашей последней синхронизации."""
