"""The frontend application package.

    app/config.py      one settings model, read from the environment and .env
    app/main.py        assembles the app: lifespan, static mount, routers
    app/controllers/   turn a request into a response
    app/services/      the rules, independent of HTTP
    app/models/        what is stored, and how it is read and written
    app/views/         templates, static assets, and how a page is put together

The dependency direction is one-way: controllers may reach into services, models
and views; services may reach into models; models and views reach into nothing
but ``config``. Anything that would need an arrow pointing back up the list is in
the wrong layer.
"""
