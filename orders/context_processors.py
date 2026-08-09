from .cart import Cart


def cart_context(request):
    return {"cart_count": len(Cart(request))}
