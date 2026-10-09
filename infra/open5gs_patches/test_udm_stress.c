#include <curl/curl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
static char location[512], stable[512];
static size_t discard(char *p, size_t s, size_t n, void *u) { return s*n; }
static size_t header(char *p, size_t s, size_t n, void *u) {
    size_t len=s*n;
    if(len>10 && !strncasecmp(p,"location: ",10)) {
        size_t k=len-10; if(k>=sizeof(location)) return 0;
        memcpy(location,p+10,k); location[k]=0;
        location[strcspn(location,"\r\n")]=0;
    }
    return len;
}
static char *readfile(char *name) {
    FILE *f=fopen(name,"rb"); if(!f) exit(2);
    fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
    char *p=calloc(n+1,1); if(!p || fread(p,1,n,f)!=(size_t)n) exit(2);
    fclose(f); return p;
}
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    char *payload[2]={readfile(argv[1]),readfile(argv[2])};
    curl_global_init(CURL_GLOBAL_DEFAULT);
    CURL *c=curl_easy_init();
    struct curl_slist *headers=curl_slist_append(NULL,"Content-Type: application/json");
    curl_easy_setopt(c,CURLOPT_URL,"http://127.0.0.12:7777/nudm-sdm/v2/imsi-999700000000004/sdm-subscriptions");
    curl_easy_setopt(c,CURLOPT_HTTP_VERSION,CURL_HTTP_VERSION_2_PRIOR_KNOWLEDGE);
    curl_easy_setopt(c,CURLOPT_NOPROXY,"*");
    curl_easy_setopt(c,CURLOPT_HTTPHEADER,headers);
    curl_easy_setopt(c,CURLOPT_WRITEFUNCTION,discard);
    curl_easy_setopt(c,CURLOPT_HEADERFUNCTION,header);
    curl_easy_setopt(c,CURLOPT_TIMEOUT,5L);
    long count200=0,count201=0; int failed=0;
    struct timespec start,end; clock_gettime(CLOCK_MONOTONIC,&start);
    /* Same URI in first half; changing URI in second half; restore at end. */
    for(int i=0;i<17001;i++) {
        int variant=(i>=8500 && i<17000) ? i%2 : 0;
        curl_easy_setopt(c,CURLOPT_POSTFIELDS,payload[variant]);
        location[0]=0;
        CURLcode rc=curl_easy_perform(c); long status=0;
        curl_easy_getinfo(c,CURLINFO_RESPONSE_CODE,&status);
        if(rc || (status!=200 && status!=201) || !location[0]) {
            fprintf(stderr,"request=%d curl=%d status=%ld\n",i,rc,status); failed=1; break;
        }
        if(!stable[0]) strcpy(stable,location);
        if(strcmp(stable,location)) { fprintf(stderr,"Resource ID changed at %d\n",i); failed=1; break; }
        if(status==200) count200++; else count201++;
        if((i+1)%4000==0) { fprintf(stderr,"completed=%d\n",i+1); }
    }
    /* Preserve the captured native callback on every exit path. */
    curl_easy_setopt(c,CURLOPT_POSTFIELDS,payload[0]);
    CURLcode restore=curl_easy_perform(c); long restore_status=0;
    curl_easy_getinfo(c,CURLINFO_RESPONSE_CODE,&restore_status);
    clock_gettime(CLOCK_MONOTONIC,&end);
    printf("{\"requests\":%ld,\"http200\":%ld,\"http201\":%ld,\"same_resource\":%s,\"location\":\"%s\",\"seconds\":%.3f,\"restore_status\":%ld}\n",
        count200+count201,count200,count201,failed?"false":"true",stable,
        end.tv_sec-start.tv_sec+(end.tv_nsec-start.tv_nsec)/1e9,restore_status);
    curl_slist_free_all(headers); curl_easy_cleanup(c); curl_global_cleanup();
    free(payload[0]); free(payload[1]);
    return failed || restore || restore_status!=200;
}
