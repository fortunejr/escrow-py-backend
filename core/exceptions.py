from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        return response

    errors = response.data
    message = "Request failed."

    if isinstance(errors, dict) and "detail" in errors:
        detail = errors.get("detail")
        if isinstance(detail, str):
            message = detail
        else:
            message = "Request failed."

    response.data = {
        "success": False,
        "message": message,
        "data": None,
        "errors": errors,
    }
    return response
