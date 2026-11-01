// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Cosmere Archives
// Separate-process VST2 host using the independently reverse-engineered FST ABI.
// No Steinberg SDK or plugin binaries are distributed with this source.
#define FST_DONT_DEPRECATE_UNKNOWN
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <cstddef>
#if defined(_WIN64) && !defined(__x86_64__)
#define __x86_64__ 1 // FST's portable pointer-width detection for MSVC x64.
#endif
#include "fst.h"
static_assert(sizeof(VstIntPtr) == sizeof(void*), "FST pointer width mismatch");
static_assert(offsetof(AEffect, uniqueID) == 112 && offsetof(AEffect, processReplacing) == 120,
              "Unexpected Windows x64 effect ABI");

static AEffect* fx = nullptr;
static HWND editor = nullptr;
static bool editorOpen = false;
static std::recursive_mutex fxLock;
static constexpr uint32_t LIMIT = 16 * 1024 * 1024;
static constexpr int BLOCK = 2048;
static double samplePosition = 0;
static VstTimeInfo timing{};

static VstIntPtr dispatch(int op, int index=0, VstIntPtr value=0, void* ptr=nullptr, float opt=0) {
    return fx->dispatcher(fx, op, index, value, ptr, opt);
}

static VstIntPtr callback(AEffect*, int op, int index, VstIntPtr value, void* ptr, float) {
    switch (op) {
    case audioMasterVersion: return 2400;
    case audioMasterGetSampleRate: return 48000;
    case audioMasterGetBlockSize: return BLOCK;
    case audioMasterGetCurrentProcessLevel: return 2;
    case audioMasterGetVendorVersion: return 1;
    case audioMasterGetVendorString:
    case audioMasterGetProductString:
        if (ptr) strcpy_s(static_cast<char*>(ptr), 64, "Cosmere Video Recording Suite");
        return 1;
    case audioMasterGetTime:
        timing.samplePos = samplePosition; timing.sampleRate = 48000;
        return reinterpret_cast<VstIntPtr>(&timing);
    case audioMasterCanDo:
        return ptr && (!strcmp(static_cast<char*>(ptr), "sizeWindow") ||
                       !strcmp(static_cast<char*>(ptr), "startStopProcess"));
    case audioMasterSizeWindow:
        if (editor && index > 0 && value > 0 && index < 4096 && value < 4096) {
            RECT rect{0, 0, index, static_cast<LONG>(value)};
            AdjustWindowRect(&rect, WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU, FALSE);
            SetWindowPos(editor, nullptr, 0, 0, rect.right-rect.left, rect.bottom-rect.top,
                         SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
            return 1;
        }
    }
    return 0;
}

static LRESULT CALLBACK windowProc(HWND hwnd, UINT msg, WPARAM w, LPARAM l) {
    std::lock_guard<std::recursive_mutex> guard(fxLock);
    if (msg == WM_CLOSE) { ShowWindow(hwnd, SW_HIDE); return 0; }
    if (msg == WM_TIMER && fx && editorOpen) { dispatch(19); return 0; } // editor idle
    if (msg == WM_APP+1) {
        if (!editorOpen) { dispatch(effEditOpen, 0, 0, hwnd); editorOpen = true; SetTimer(hwnd, 1, 33, nullptr); }
        ERect* rect = nullptr;
        dispatch(effEditGetRect, 0, 0, &rect);
        if (rect) callback(fx, audioMasterSizeWindow, rect->right-rect->left, rect->bottom-rect->top, nullptr, 0);
        ShowWindow(hwnd, SW_SHOW); SetForegroundWindow(hwnd);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, w, l);
}

static bool readBytes(void* data, size_t size) {
    return !size || fread(data, 1, size, stdin) == size;
}
static void reply(uint32_t status, const void* data, uint32_t size) {
    fwrite(&status, 4, 1, stdout); fwrite(&size, 4, 1, stdout);
    if (size) fwrite(data, 1, size, stdout);
    fflush(stdout);
}
static void error(const char* text) { reply(1, text, static_cast<uint32_t>(strlen(text))); }
static std::string escape(const char* text) {
    std::string out;
    for (const unsigned char c : std::string(text)) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c >= 32 && c < 127) out += c;
    }
    return out;
}
static std::vector<uint8_t> state() {
    std::vector<uint8_t> data(12);
    memcpy(data.data(), "CVS1", 4); memcpy(data.data()+4, &fx->uniqueID, 4);
    void* chunk = nullptr;
    VstIntPtr size = (fx->flags & effFlagsProgramChunks) ? dispatch(effGetChunk, 0, 0, &chunk) : 0;
    if (size > LIMIT-12) return {}; // Never silently lose opaque profile data.
    uint32_t mode = size > 0 && chunk && size <= LIMIT-12 ? 1 : 0;
    memcpy(data.data()+8, &mode, 4);
    if (mode) {
        data.insert(data.end(), static_cast<uint8_t*>(chunk), static_cast<uint8_t*>(chunk)+size);
    } else {
        for (int i=0; i<fx->numParams; ++i) {
            float value = fx->getParameter(fx, i);
            const auto* bytes = reinterpret_cast<uint8_t*>(&value);
            data.insert(data.end(), bytes, bytes+4);
        }
    }
    return data;
}

static void commands() {
    uint32_t op, size;
    while (readBytes(&op, 4) && readBytes(&size, 4) && size <= LIMIT) {
        std::vector<uint8_t> data(size);
        if (!readBytes(data.data(), size)) break;
        if (op == 9) { ShowWindow(editor, SW_HIDE); reply(0, nullptr, 0); continue; }
        if (op == 3) { // vendor editor creation must be on its Windows UI thread
            if (!(fx->flags & effFlagsHasEditor)) { error("Plugin has no native editor"); continue; }
            SendMessageW(editor, WM_APP+1, 0, 0); reply(0, nullptr, 0); continue;
        }
        std::lock_guard<std::recursive_mutex> guard(fxLock);
        if (op == 0) {
            char name[128]{}; dispatch(effGetEffectName, 0, 0, name);
            std::string meta = "{\"name\":\"" + escape(name) + "\",\"inputs\":" + std::to_string(fx->numInputs)
                + ",\"outputs\":" + std::to_string(fx->numOutputs) + ",\"latency\":" + std::to_string(std::max(0, fx->initialDelay))
                + ",\"parameters\":" + std::to_string(fx->numParams) + ",\"editor\":" + ((fx->flags & effFlagsHasEditor) ? "true}" : "false}");
            reply(0, meta.data(), static_cast<uint32_t>(meta.size()));
        } else if (op == 1) { // one mono float32 block; main stereo duplicates, sidechains silent
            if (size % 4 || size/4 > BLOCK) { error("Invalid audio block"); continue; }
            int count = static_cast<int>(size/4);
            std::vector<std::vector<float>> in(fx->numInputs, std::vector<float>(count));
            std::vector<std::vector<float>> out(fx->numOutputs, std::vector<float>(count));
            std::vector<float*> inputs, outputs;
            for (int ch=0; ch<fx->numInputs; ++ch) {
                if (ch < 2) memcpy(in[ch].data(), data.data(), size);
                inputs.push_back(in[ch].data());
            }
            for (auto& channel : out) outputs.push_back(channel.data());
            fx->processReplacing(fx, inputs.data(), outputs.data(), count);
            std::vector<float> mono(count);
            int channels = std::min(2, fx->numOutputs);
            for (int i=0; i<count; ++i) {
                for (int ch=0; ch<channels; ++ch) mono[i] += out[ch][i]/channels;
                if (!std::isfinite(mono[i])) mono[i] = 0;
            }
            samplePosition += count;
            reply(0, mono.data(), size);
        } else if (op == 4) {
            if (size != 8) { error("Invalid parameter"); continue; }
            uint32_t index; float value;
            memcpy(&index, data.data(), 4); memcpy(&value, data.data()+4, 4);
            if (index >= static_cast<uint32_t>(fx->numParams) || !std::isfinite(value) || value < 0 || value > 1) {
                error("Parameter outside range"); continue;
            }
            fx->setParameter(fx, index, value); reply(0, nullptr, 0);
        } else if (op == 5) {
            auto saved = state();
            if (saved.empty()) { error("Plugin state exceeds 16 MiB; cannot save safely"); continue; }
            reply(0, saved.data(), static_cast<uint32_t>(saved.size()));
        } else if (op == 6) {
            int32_t id; uint32_t mode;
            if (size < 12 || memcmp(data.data(), "CVS1", 4)) { error("Invalid saved state"); continue; }
            memcpy(&id, data.data()+4, 4); memcpy(&mode, data.data()+8, 4);
            if (id != fx->uniqueID) { error("State belongs to another plugin"); continue; }
            if (mode == 1 && size > 12 && (fx->flags & effFlagsProgramChunks)) {
                dispatch(effSetChunk, 0, size-12, data.data()+12);
            } else if (mode == 0 && size-12 == static_cast<uint32_t>(fx->numParams)*4) {
                for (int i=0; i<fx->numParams; ++i) {
                    float value; memcpy(&value, data.data()+12+i*4, 4);
                    if (std::isfinite(value)) fx->setParameter(fx, i, std::clamp(value, 0.f, 1.f));
                }
            } else { error("Invalid state payload"); continue; }
            reply(0, nullptr, 0);
        } else if (op == 7) {
            std::string json = "[";
            for (int i=0; i<fx->numParams; ++i) {
                char name[128]{}; dispatch(effGetParamName, i, 0, name);
                if (i) json += ",";
                json += "{\"name\":\"" + escape(name) + "\",\"value\":" + std::to_string(fx->getParameter(fx, i)) + "}";
            }
            json += "]"; reply(0, json.data(), static_cast<uint32_t>(json.size()));
        } else if (op == 8) { reply(0, nullptr, 0); break; }
        else error("Unknown host command");
    }
    PostMessageW(editor, WM_QUIT, 0, 0);
    // WM_QUIT belongs to the message thread queue, not a window.
    PostThreadMessageW(GetWindowThreadProcessId(editor, nullptr), WM_QUIT, 0, 0);
}

int wmain(int argc, wchar_t** argv) {
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
    if (argc != 2) return 2;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    HMODULE module = LoadLibraryExW(argv[1], nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) { error("Could not load 64-bit plugin DLL"); return 3; }
    using Entry = AEffect* (*)(audioMasterCallback);
    auto entry = reinterpret_cast<Entry>(GetProcAddress(module, "VSTPluginMain"));
    if (!entry) entry = reinterpret_cast<Entry>(GetProcAddress(module, "main"));
    if (!entry) { error("DLL is not a VST2 plugin"); return 4; }
    fx = entry(callback);
    if (!fx || fx->magic != 0x56737450 || !fx->dispatcher || !fx->processReplacing ||
        fx->numInputs < 1 || fx->numInputs > 64 || fx->numOutputs < 1 || fx->numOutputs > 64 ||
        fx->numParams < 0 || fx->numParams > 65536 || (fx->flags & effFlagsIsSynth)) {
        error("Unsupported effect; use a 64-bit VST2 audio effect"); return 5;
    }
    dispatch(effOpen); dispatch(effSetSampleRate, 0, 0, nullptr, 48000.f);
    dispatch(effSetBlockSize, 0, BLOCK); dispatch(effMainsChanged, 0, 1); dispatch(effStartProcess);
    WNDCLASSW cls{}; cls.lpfnWndProc=windowProc; cls.hInstance=GetModuleHandleW(nullptr);
    cls.lpszClassName=L"CosmereVST2Editor"; cls.hCursor=LoadCursor(nullptr, IDC_ARROW);
    RegisterClassW(&cls);
    editor=CreateWindowExW(0, cls.lpszClassName, L"Plugin — Cosmere Video Recording Suite",
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU, CW_USEDEFAULT, CW_USEDEFAULT, 800, 600,
        nullptr, nullptr, cls.hInstance, nullptr);
    if (!editor) return 6;
    std::thread io(commands);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    io.join();
    if (editorOpen) dispatch(effEditClose);
    dispatch(effStopProcess); dispatch(effMainsChanged, 0, 0); dispatch(effClose);
    DestroyWindow(editor); FreeLibrary(module);
    return 0;
}
