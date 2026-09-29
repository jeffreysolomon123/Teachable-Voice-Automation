package com.example.flowlaunchertest

data class TargetApp(val name: String, val pkg: String)

val TARGET_APPS = listOf(
    TargetApp("Swiggy", "in.swiggy.android"),
    TargetApp("Zomato", "com.application.zomato"),
    TargetApp("Flipkart", "com.flipkart.android"),
)
