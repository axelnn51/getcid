#include <windows.h>
#include <stdio.h>
#include <wchar.h>

// Structs needed by PidGenX
typedef struct _PID_INFO {
    DWORD dwSize;
    WCHAR szPid[24];
    WCHAR szSkuId[32];
    WCHAR szLicenseType[32];
    WCHAR szOemId[32];
    WCHAR szGroup1[32];
    WCHAR szGroup2[32];
    DWORD dwCryptoId;
} PID_INFO, *PPID_INFO;

typedef struct _DIGITAL_PRODUCT_ID {
    DWORD dwSize;
    BYTE bData[1200];
} DIGITAL_PRODUCT_ID, *PDIGITAL_PRODUCT_ID;

typedef HRESULT (__stdcall *PidGenX_t)(
    const WCHAR* pwszKey,
    const WCHAR* pwszPkeyConfig,
    const WCHAR* pwszProductId,
    int unknown,
    void* pPidInfo,
    void* pDigitalProductId,
    void* pDigitalProductId4
);

// Escape JSON strings
void escape_json(const WCHAR* input, char* output) {
    int j = 0;
    for (int i = 0; input[i] != L'\0'; i++) {
        switch (input[i]) {
            case L'\"': output[j++] = '\\'; output[j++] = '\"'; break;
            case L'\\': output[j++] = '\\'; output[j++] = '\\'; break;
            case L'\n': output[j++] = '\\'; output[j++] = 'n'; break;
            case L'\r': output[j++] = '\\'; output[j++] = 'r'; break;
            case L'\t': output[j++] = '\\'; output[j++] = 't'; break;
            default:
                if (input[i] < 32 || input[i] > 126) {
                    j += sprintf(&output[j], "\\u%04x", (int)input[i]);
                } else {
                    output[j++] = (char)input[i];
                }
                break;
        }
    }
    output[j] = '\0';
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        printf("{\"error\": \"Usage: pidchecker.exe <key> <pkeyconfig_path>\"}\n");
        return 1;
    }
    
    HMODULE hLib = LoadLibraryA("pidgenx.dll");
    if (!hLib) {
        printf("{\"error\": \"Failed to load pidgenx.dll. Error: %lu\"}\n", GetLastError());
        return 1;
    }
    
    PidGenX_t pPidGenX = (PidGenX_t)GetProcAddress(hLib, "PidGenX");
    if (!pPidGenX) {
        printf("{\"error\": \"Failed to find PidGenX in dll\"}\n");
        return 1;
    }
    
    WCHAR wKey[64];
    MultiByteToWideChar(CP_UTF8, 0, argv[1], -1, wKey, 64);
    
    WCHAR wConfig[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, wConfig, MAX_PATH);
    
    PID_INFO pidInfo = {0};
    pidInfo.dwSize = sizeof(PID_INFO);
    
    DIGITAL_PRODUCT_ID dpi = {0};
    dpi.dwSize = sizeof(dpi);
    
    // Call PidGenX
    HRESULT hr = pPidGenX(wKey, wConfig, L"00000", 0, &pidInfo, &dpi, NULL);
    
    if (hr == 0 || hr == 0x8A010001) { 
        // 0 = S_OK (Valid)
        char escPid[256], escSku[256], escType[256], escOem[256];
        escape_json(pidInfo.szPid, escPid);
        escape_json(pidInfo.szSkuId, escSku);
        escape_json(pidInfo.szLicenseType, escType);
        escape_json(pidInfo.szOemId, escOem);
        
        printf("{\"is_valid\": true, \"crypto_id\": %lu, \"pid\": \"%s\", \"sku\": \"%s\", \"license_type\": \"%s\", \"oem_id\": \"%s\", \"error_code\": \"0x%lX\"}\n",
               pidInfo.dwCryptoId, escPid, escSku, escType, escOem, hr);
    } else {
        printf("{\"is_valid\": false, \"error_code\": \"0x%lX\"}\n", hr);
    }
    
    FreeLibrary(hLib);
    return 0;
}
